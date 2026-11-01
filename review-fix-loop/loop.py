#!/usr/bin/env python3
"""评审-修复自动迭代程序。

每轮先用评审模型调用 code-review-doc 技能，再用修复模型调用 tdd-implement 技能阶段五，
直到评审报告中不再出现严重和高级别问题。技能内容不在本程序中复制，始终加载 skills 目录中的最新版本。
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
SKILLS_DIR = TOOL_DIR.parent
CACHE_DIR = Path.home() / ".cache" / "review-fix-loop"
LEVELS = ("严重", "高", "中", "低")
ISSUE_RE = re.compile(r"^#{2,4}\s*问题\s*\d+\s*[（(]\s*(严重|高|中|低)\s*[)）]", re.M)
STATUS_RE = re.compile(r"LOOP_STATUS:\s*(OK|NEED_HUMAN)")


class LoopStop(Exception):
    """需要停止循环并交给人处理的情况。"""


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_json_lenient(path):
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 兼容手工编辑时末尾漏掉右花括号的配置文件
        for suffix in ("}", "}}"):
            try:
                data = json.loads(text.rstrip() + suffix)
                log(f"警告：{path} 不是合法 JSON，已按补全 '{suffix}' 解析，建议修正该文件")
                return data
            except json.JSONDecodeError:
                pass
        raise


class Role:
    """一个模型角色（评审或修复）的运行配置。"""

    def __init__(self, name, cfg):
        self.name = name
        env = {}
        if cfg.get("settings_file"):
            src = Path(os.path.expanduser(cfg["settings_file"]))
            env.update(load_json_lenient(src).get("env", {}))
        env.update(cfg.get("env", {}))
        if cfg.get("base_url"):
            env["ANTHROPIC_BASE_URL"] = cfg["base_url"]
        if cfg.get("auth_token"):
            env["ANTHROPIC_AUTH_TOKEN"] = cfg["auth_token"]
        if not env.get("ANTHROPIC_BASE_URL") or not (env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")):
            raise SystemExit(f"[{name}] 缺少 base_url 或 auth_token 配置")
        self.env = {k: str(v) for k, v in env.items()}
        self.model = cfg.get("model", "")
        self.prompt_file = TOOL_DIR / cfg.get("prompt", f"prompts/{name}.md")
        self.config_dir = CACHE_DIR / "config" / name
        self._prepare_config_dir()

    def _prepare_config_dir(self):
        # 每个角色使用独立的配置目录，不影响 ~/.claude 与正在运行的交互会话
        self.config_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.config_dir, 0o700)
        settings = {"env": self.env, "skipDangerousModePermissionPrompt": True}
        target = self.config_dir / "settings.json"
        target.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(target, 0o600)
        link = self.config_dir / "skills"
        if link.is_symlink() or link.exists():
            if link.is_symlink() and link.resolve() == SKILLS_DIR:
                return
            link.unlink() if link.is_symlink() else shutil.rmtree(link)
        link.symlink_to(SKILLS_DIR)

    def proc_env(self):
        # 去掉父进程继承的模型与会话变量，避免请求发错服务
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE"))}
        env.update(self.env)
        env["CLAUDE_CONFIG_DIR"] = str(self.config_dir)
        return env

    def describe(self):
        model = self.model or self.env.get("ANTHROPIC_MODEL", "服务端默认")
        return f"{self.name}: {self.env['ANTHROPIC_BASE_URL']}，模型 {model}"

    def run(self, prompt, cwd, log_file, timeout_s, max_turns=None):
        cmd = ["claude", "-p", "--output-format", "json", "--permission-mode", "bypassPermissions",
               "--no-session-persistence"]
        if self.model:
            cmd += ["--model", self.model]
        if max_turns:
            cmd += ["--max-turns", str(max_turns)]
        try:
            proc = subprocess.run(cmd, input=prompt, cwd=cwd, env=self.proc_env(), capture_output=True,
                                  text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            raise LoopStop(f"[{self.name}] 调用超时（{timeout_s} 秒）")
        Path(log_file).write_text(proc.stdout + ("\n--- stderr ---\n" + proc.stderr if proc.stderr else ""),
                                  encoding="utf-8")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise LoopStop(f"[{self.name}] 输出无法解析（退出码 {proc.returncode}），见 {log_file}")
        if data.get("is_error") or proc.returncode != 0:
            raise LoopStop(f"[{self.name}] 调用失败：{str(data.get('result', ''))[:300]}，见 {log_file}")
        return data


def git(project, *args):
    return subprocess.run(["git", *args], cwd=project, capture_output=True, text=True, check=True).stdout


def code_fingerprint(project):
    """docs/ 以外代码的未提交状态指纹，用于确认评审过程没有修改代码。"""
    h = hashlib.sha256()
    h.update(git(project, "diff", "HEAD", "--", ".", ":(exclude)docs").encode())
    for f in sorted(git(project, "ls-files", "--others", "--exclude-standard", "--", ".", ":(exclude)docs").split()):
        h.update(f.encode())
        p = Path(project) / f
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def find_doc(project, kind, req_id):
    files = sorted(Path(project, "docs").glob(f"{kind}-{req_id}-*.md"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def count_issues(text):
    counts = dict.fromkeys(LEVELS, 0)
    for level in ISSUE_RE.findall(text):
        counts[level] += 1
    return counts


def render(template_path, values):
    text = template_path.read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def fmt_counts(c):
    return "｜".join(f"{k} {c[k]}" for k in LEVELS)


def preflight(cfg, roles):
    project = Path(cfg["project"]["path"])
    req_id = cfg["project"]["req_id"]
    if not (project / ".git").exists():
        raise SystemExit(f"{project} 不是 git 仓库")
    for kind in ("request", "design", "implement"):
        if not find_doc(project, kind, req_id):
            raise SystemExit(f"缺少 docs/{kind}-{req_id}-*.md")
    if not git(project, "status", "--porcelain", "--", ".", ":(exclude)docs").strip():
        raise SystemExit("docs/ 以外没有未提交的代码改动，无可评审内容")
    for role in roles.values():
        log(f"连通测试 {role.describe()}")
        out = CACHE_DIR / "config" / role.name / "ping.json"
        data = role.run("只回复两个字母：OK", project, out, 300, max_turns=1)
        log(f"  返回：{str(data.get('result', '')).strip()[:40]}")


def main():
    ap = argparse.ArgumentParser(description="评审-修复自动迭代")
    ap.add_argument("-c", "--config", default=str(TOOL_DIR / "config.toml"))
    ap.add_argument("--check", action="store_true", help="只做准备检查与连通测试")
    args = ap.parse_args()

    cfg = tomllib.loads(Path(args.config).read_text(encoding="utf-8"))
    loop_cfg = cfg.get("loop", {})
    project = Path(cfg["project"]["path"]).resolve()
    req_id = cfg["project"]["req_id"]
    test_cmd = cfg["project"].get("test_cmd", "")
    timeout_s = int(loop_cfg.get("timeout_minutes", 60)) * 60
    roles = {name: Role(name, cfg[name]) for name in ("reviewer", "fixer")}

    preflight(cfg, roles)
    if args.check:
        log("准备检查通过")
        return

    run_dir = CACHE_DIR / "runs" / f"{project.name}-{req_id}" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}"
    run_dir.mkdir(parents=True)
    log(f"运行目录：{run_dir}")
    base = f"{git(project, 'rev-parse', '--abbrev-ref', 'HEAD').strip()}@{git(project, 'rev-parse', '--short', 'HEAD').strip()}"
    history, best, stall, outcome = [], None, 0, ""

    try:
        for rnd in range(1, int(loop_cfg.get("max_rounds", 5)) + 1):
            rdir = run_dir / f"round-{rnd}"
            rdir.mkdir()
            implement = find_doc(project, "implement", req_id)
            values = {"round": rnd, "req_id": req_id, "project": project, "test_cmd": test_cmd,
                      "implement_file": implement.relative_to(project)}

            # 评审
            log(f"第 {rnd} 轮评审开始")
            before_fp = code_fingerprint(project)
            started = dt.datetime.now().timestamp()
            roles["reviewer"].run(render(roles["reviewer"].prompt_file, values), project,
                                  rdir / "review.log.json", timeout_s)
            if code_fingerprint(project) != before_fp:
                (rdir / "reviewer-changes.patch").write_text(git(project, "diff", "HEAD"), encoding="utf-8")
                raise LoopStop("评审过程修改了 docs/ 以外的代码，已停止，改动见 reviewer-changes.patch")
            review = find_doc(project, "review", req_id)
            if not review or review.stat().st_mtime < started:
                raise LoopStop("本轮评审没有生成或更新 review 文档")
            shutil.copy2(review, rdir / review.name)
            counts = count_issues(review.read_text(encoding="utf-8"))
            history.append(counts)
            blocking = counts["严重"] + counts["高"]
            log(f"第 {rnd} 轮评审结果：{fmt_counts(counts)}（{review.name}）")
            if blocking == 0:
                outcome = f"通过：第 {rnd} 轮评审没有严重和高级别问题"
                break
            if best is not None and blocking >= best:
                stall += 1
                if stall >= int(loop_cfg.get("no_progress_max", 2)):
                    raise LoopStop(f"连续 {stall} 轮严重与高级别问题数量没有下降")
            else:
                best, stall = blocking, 0

            # 修复
            log(f"第 {rnd} 轮修复开始")
            values["review_file"] = review.relative_to(project)
            data = roles["fixer"].run(render(roles["fixer"].prompt_file, values), project,
                                      rdir / "fix.log.json", timeout_s)
            (rdir / "changes.patch").write_text(git(project, "diff", "HEAD"), encoding="utf-8")
            implement = find_doc(project, "implement", req_id)
            shutil.copy2(implement, rdir / implement.name)
            status = STATUS_RE.findall(str(data.get("result", "")))
            if not status:
                raise LoopStop("修复进程没有输出 LOOP_STATUS，见 fix.log.json")
            if status[-1] == "NEED_HUMAN":
                raise LoopStop("修复进程报告存在需要人工确认的事项，见实施文档待确认事项")
            if test_cmd:
                t = subprocess.run(test_cmd, shell=True, cwd=project, capture_output=True, text=True,
                                   timeout=timeout_s)
                (rdir / "test.log").write_text(t.stdout + t.stderr, encoding="utf-8")
                if t.returncode != 0:
                    raise LoopStop(f"第 {rnd} 轮修复后测试失败，见 {rdir / 'test.log'}")
                log(f"第 {rnd} 轮修复后测试通过")
        else:
            outcome = "停止：达到最大轮数仍有严重或高级别问题"
    except LoopStop as e:
        outcome = f"停止：{e}"

    if outcome.startswith("通过") and loop_cfg.get("run_techdoc", False):
        role = roles[loop_cfg.get("techdoc_role", "fixer")]
        log("生成最终技术文档")
        try:
            role.run(render(TOOL_DIR / "prompts" / "techdoc.md", {"req_id": req_id, "project": project,
                                                                   "test_cmd": test_cmd}),
                     project, run_dir / "techdoc.log.json", timeout_s)
            doc = find_doc(project, "techdoc", req_id)
            outcome += f"；技术文档：{doc.relative_to(project) if doc else '未找到生成的文件'}"
        except LoopStop as e:
            outcome += f"；技术文档生成失败：{e}"

    lines = [f"# 评审修复迭代汇总：{req_id}", "", f"- 项目：{project}", f"- 起始状态：{base}",
             f"- {roles['reviewer'].describe()}", f"- {roles['fixer'].describe()}", f"- 结论：{outcome}", "",
             "| 轮次 | 严重 | 高 | 中 | 低 |", "|------|------|----|----|----|"]
    lines += [f"| {i} | " + " | ".join(str(c[k]) for k in LEVELS) + " |" for i, c in enumerate(history, 1)]
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(outcome)
    log(f"汇总：{run_dir / 'summary.md'}")
    sys.exit(0 if outcome.startswith("通过") else 1)


if __name__ == "__main__":
    main()
