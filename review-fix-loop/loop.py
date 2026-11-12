#!/usr/bin/env python3
"""评审-修复自动迭代程序。

每轮先用评审模型调用 code-review-doc 技能，再用修复模型调用 tdd-implement 技能阶段五，
评审发现的问题都会修复；阻塞级别（默认严重、高）清零后修完剩余问题即结束，不再开新一轮评审。技能内容不在本程序中复制，始终加载 skills 目录中的最新版本。
评审与修复可分别使用 claude、codex、kimi 三种 CLI；每次调用都是新的进程与对话，结束后不保留会话。
评审范围默认只看工作区未提交改动；repos 里给某个仓库配了 base/head 时，改为评审该提交区间
加上工作区未提交改动，修复改动始终留在工作区。
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
from dataclasses import dataclass
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_SKILLS_DIR = TOOL_DIR.parent
CACHE_DIR = Path.home() / ".cache" / "review-fix-loop"
LEVELS = ("严重", "高", "中", "低")
ISSUE_RE = re.compile(r"^#{2,4}\s*问题\s*\d+\s*[（(]\s*(严重|高|中|低)\s*[)）]", re.M)
STATUS_RE = re.compile(r"LOOP_STATUS:\s*(OK|NEED_HUMAN)")


@dataclass(frozen=True)
class RepoSpec:
    """一个参与评审的仓库。base 为空时只评审未提交改动（旧行为）。"""
    path: Path
    base: str | None = None
    head: str = "HEAD"


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


PROXY_KEYS = ("http_proxy", "https_proxy", "all_proxy", "no_proxy",
              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
KIMI_HOME = Path.home() / ".kimi-code"


def setup_proxy(pcfg):
    """执行配置中的代理开启命令（如 clashon、clash mode global），返回其导出的代理环境变量。"""
    script = "\n".join(pcfg.get("commands", [])) + "\nenv -0"
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise SystemExit(f"代理开启命令失败：{proc.stderr.strip()[:300]}")
    env = dict(item.split("=", 1) for item in proc.stdout.split("\0") if "=" in item)
    env = {k: env[k] for k in PROXY_KEYS if k in env}
    env.update(pcfg.get("env", {}))
    if not env:
        raise SystemExit("代理开启命令执行后没有得到 http_proxy/https_proxy，请在 [proxy.env] 中直接指定")
    check = pcfg.get("check_url", "")
    if check:
        r = subprocess.run(["curl", "-sI", "-m", "15", check], env={**os.environ, **env},
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"代理已开启但无法访问 {check}")
    log(f"代理已开启：{env.get('https_proxy') or env.get('HTTPS_PROXY')}")
    return env


class Role:
    """一个模型角色（评审或修复）的运行配置，engine 决定用哪个 CLI 执行。"""

    def __init__(self, name, cfg, add_dirs, skills_dir, proxy_env=None):
        self.name = name
        self.skills_dir = Path(os.path.expanduser(cfg.get("skills_dir", skills_dir))).resolve()
        if not self.skills_dir.is_dir():
            raise SystemExit(f"[{name}] 技能目录不存在：{self.skills_dir}")
        self.engine = cfg.get("engine", "claude")
        if self.engine not in ("claude", "codex", "kimi"):
            raise SystemExit(f"[{name}] 不支持的 engine：{self.engine}")
        self.model = cfg.get("model", "")
        self.args = [str(a) for a in cfg.get("args", [])]
        self.add_dirs = add_dirs
        self.clear_session = cfg.get("clear_session", True)
        self.env = {k: str(v) for k, v in cfg.get("env", {}).items()}
        # 默认最高强度思考，可在角色 env 中覆盖
        if self.engine == "kimi":
            self.env.setdefault("KIMI_MODEL_THINKING_EFFORT", "max")
        elif self.engine == "claude":
            self.env.setdefault("CLAUDE_CODE_EFFORT_LEVEL", "max")
        self.proxy_env = proxy_env or {}
        if self.engine == "claude":
            self._init_claude(cfg)

    def _init_claude(self, cfg):
        env = {}
        if cfg.get("settings_file"):
            src = Path(os.path.expanduser(cfg["settings_file"]))
            env.update(load_json_lenient(src).get("env", {}))
        env.update(self.env)
        if cfg.get("base_url"):
            env["ANTHROPIC_BASE_URL"] = cfg["base_url"]
        if cfg.get("auth_token"):
            env["ANTHROPIC_AUTH_TOKEN"] = cfg["auth_token"]
        if not env.get("ANTHROPIC_BASE_URL") or not (env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")):
            raise SystemExit(f"[{self.name}] 缺少 base_url 或 auth_token 配置")
        self.env = {k: str(v) for k, v in env.items()}
        self.config_dir = CACHE_DIR / "config" / self.name
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
            if link.is_symlink() and link.resolve() == self.skills_dir:
                return
            link.unlink() if link.is_symlink() else shutil.rmtree(link)
        link.symlink_to(self.skills_dir)

    def proc_env(self):
        env = dict(os.environ)
        if self.engine == "claude":
            # 去掉父进程继承的模型与会话变量，避免请求发错服务
            env = {k: v for k, v in env.items() if not k.startswith(("ANTHROPIC_", "CLAUDE"))}
            env["CLAUDE_CONFIG_DIR"] = str(self.config_dir)
        env.update(self.proxy_env)
        env.update(self.env)
        return env

    def describe(self):
        if self.engine == "claude":
            model = self.model or self.env.get("ANTHROPIC_MODEL", "服务端默认")
            where = f"claude 密钥登录 {self.env['ANTHROPIC_BASE_URL']}"
        else:
            model = self.model or "CLI 默认"
            where = f"{self.engine} 账号登录"
        proxy = "，走代理" if self.proxy_env else ""
        return f"{self.name}: {where}，模型 {model}，技能目录 {self.skills_dir}{proxy}"

    def wrap_prompt(self, prompt):
        # codex、kimi 不会自动发现 ~/.claude/skills，明确告诉它们技能文件的位置
        if self.engine in ("codex", "kimi"):
            return (f"技能目录：{self.skills_dir}。提示词中要求使用某个技能时，先完整阅读 "
                    f"{self.skills_dir}/<技能名>/SKILL.md 及其引用的文件，再严格按技能执行。\n\n{prompt}")
        return prompt

    def run(self, prompt, cwd, log_file, timeout_s, max_turns=None):
        """执行一次全新的对话并返回最终回复文本；对话结束后不保留会话记录。"""
        prompt = self.wrap_prompt(prompt)
        log_file = Path(log_file)
        last_msg = log_file.with_suffix(".last.txt")
        stdin = prompt
        if self.engine == "claude":
            # stream-json 会记录每一步工具调用，便于在日志中确认加载了哪些技能
            cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose",
                   "--permission-mode", "bypassPermissions", "--no-session-persistence"]
            if max_turns:
                cmd += ["--max-turns", str(max_turns)]
            for d in self.add_dirs:
                cmd += ["--add-dir", str(d)]
        elif self.engine == "codex":
            cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "--color", "never",
                   "--dangerously-bypass-approvals-and-sandbox", "-C", str(cwd), "-o", str(last_msg)]
            for d in self.add_dirs:
                cmd += ["--add-dir", str(d)]
        else:
            cmd = ["kimi", "--output-format", "stream-json", "--skills-dir", str(self.skills_dir), "-p", prompt]
            for d in self.add_dirs:
                cmd += ["--add-dir", str(d)]
            stdin = None
        if self.model:
            cmd += ["--model", self.model]
        cmd += self.args
        if self.engine == "codex":
            cmd.append("-")
        try:
            proc = subprocess.run(cmd, input=stdin, cwd=cwd, env=self.proc_env(), capture_output=True,
                                  text=True, timeout=timeout_s, stdin=None if stdin is not None else subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise LoopStop(f"[{self.name}] 调用超时（{timeout_s} 秒）")
        log_file.write_text(proc.stdout + ("\n--- stderr ---\n" + proc.stderr if proc.stderr else ""),
                            encoding="utf-8")
        if self.engine == "claude":
            return self._result_claude(proc, log_file)
        if self.engine == "codex":
            result = last_msg.read_text(encoding="utf-8") if last_msg.exists() else ""
        else:
            result = self._result_kimi(proc.stdout)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip()[-300:]
            raise LoopStop(f"[{self.name}] 调用失败（退出码 {proc.returncode}）：{tail}，见 {log_file}")
        return result

    def _result_claude(self, proc, log_file):
        data = None
        for line in proc.stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "result":
                data = ev
        if data is None:
            raise LoopStop(f"[{self.name}] 输出无法解析（退出码 {proc.returncode}），见 {log_file}")
        if data.get("is_error") or proc.returncode != 0:
            raise LoopStop(f"[{self.name}] 调用失败：{str(data.get('result', ''))[:300]}，见 {log_file}")
        return str(data.get("result", ""))

    def _result_kimi(self, stdout):
        result, session_id = "", None
        for line in stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("role") == "assistant" and isinstance(ev.get("content"), str) and ev["content"].strip():
                result = ev["content"]
            elif ev.get("type") == "session.resume_hint":
                session_id = ev.get("session_id")
        if session_id and self.clear_session:
            self._drop_kimi_session(session_id)
        return result

    def _drop_kimi_session(self, session_id):
        # kimi 没有不落盘选项，结束后删除本次会话目录及索引行，保证每轮都是干净的对话
        index = KIMI_HOME / "session_index.jsonl"
        sessions_root = (KIMI_HOME / "sessions").resolve()
        for d in sessions_root.glob(f"*/{session_id}"):
            if d.resolve().is_relative_to(sessions_root):
                shutil.rmtree(d, ignore_errors=True)
                if not any(d.parent.iterdir()):
                    d.parent.rmdir()
        for marker in (sessions_root / ".index-dirty").glob(f"{session_id}.*"):
            marker.unlink(missing_ok=True)
        if index.exists():
            lines = index.read_text(encoding="utf-8").splitlines(keepends=True)
            kept = [l for l in lines if f'"{session_id}"' not in l]
            if len(kept) != len(lines):
                index.write_text("".join(kept), encoding="utf-8")


def git(project, *args):
    return subprocess.run(["git", *args], cwd=project, capture_output=True, text=True, check=True).stdout


def excludes(repo, docs_dir):
    """仓库内的 docs 目录不计入代码改动。"""
    try:
        return [f":(exclude){docs_dir.relative_to(repo)}"]
    except ValueError:
        return []


def committed_diff(spec, docs_dir=None):
    """base..head 的已提交改动；没配 base 时为空。"""
    if not spec.base:
        return ""
    ex = excludes(spec.path, docs_dir) if docs_dir else []
    return git(spec.path, "diff", f"{spec.base}..{spec.head}", "--", ".", *ex)


def worktree_diff(spec, docs_dir=None):
    """工作区相对当前 HEAD 的未提交改动。"""
    ex = excludes(spec.path, docs_dir) if docs_dir else []
    return git(spec.path, "diff", "HEAD", "--", ".", *ex)


def untracked_files(spec, docs_dir=None):
    """工作区里未跟踪的文件（不含 docs）。"""
    ex = excludes(spec.path, docs_dir) if docs_dir else []
    return sorted(git(spec.path, "ls-files", "--others", "--exclude-standard", "--", ".", *ex).splitlines())


def code_fingerprint(specs, docs_dir):
    """各仓库评审范围内 docs/ 以外代码的指纹，用于确认评审过程没有修改代码。"""
    h = hashlib.sha256()
    for spec in specs:
        repo = spec.path
        h.update(str(repo).encode())
        # HEAD 也计入指纹：评审如果自己提交了改动，同样要能发现。
        h.update(git(repo, "rev-parse", "HEAD").encode())
        h.update(committed_diff(spec, docs_dir).encode())
        h.update(worktree_diff(spec, docs_dir).encode())
        for f in untracked_files(spec, docs_dir):
            h.update(f.encode())
            p = repo / f
            if p.is_file():
                h.update(p.read_bytes())
    return h.hexdigest()


def all_diffs(specs):
    """所有仓库评审范围内的 diff：已提交区间 + 工作区未提交改动。"""
    parts = []
    for spec in specs:
        parts.append(f"### 仓库 {spec.path}\n")
        if spec.base:
            parts.append(f"#### 已提交区间 {spec.base}..{spec.head}\n"
                         f"{git(spec.path, 'diff', f'{spec.base}..{spec.head}')}\n")
        parts.append(f"#### 工作区未提交改动（相对 HEAD）\n{git(spec.path, 'diff', 'HEAD')}\n")
    return "".join(parts)


def review_scope_text(specs):
    """写进提示词的评审范围说明，与 loop.py 实际统计的口径一致。"""
    lines = []
    for spec in specs:
        if spec.base:
            suffix = ""
            if git(spec.path, "rev-parse", spec.head).strip() != git(spec.path, "rev-parse", "HEAD").strip():
                suffix = "（head 早于当前 HEAD，区间文件可能已被后续提交改动；定位以当前工作区为准）"
            lines.append(f"- {spec.path}：`git diff {spec.base}..{spec.head}`（已提交区间）"
                         f" + `git diff HEAD`（工作区未提交改动）{suffix}")
        else:
            lines.append(f"- {spec.path}：`git diff HEAD`（工作区未提交改动）+ 未跟踪文件")
    return "\n".join(lines)


def find_doc(docs_dir, kind, req_id):
    files = sorted(docs_dir.glob(f"{kind}-{req_id}-*.md"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def count_issues(text):
    counts = dict.fromkeys(LEVELS, 0)
    for level in ISSUE_RE.findall(text):
        counts[level] += 1
    return counts


def fmt_counts(c):
    return "｜".join(f"{k} {c[k]}" for k in LEVELS)


def load_project(cfg):
    pc = cfg["project"]
    raw = pc.get("repos") or ([pc["path"]] if pc.get("path") else None)
    if not raw:
        raise SystemExit("[project] 需要 repos 或 path")
    specs = []
    for item in raw:
        if isinstance(item, str):
            specs.append(RepoSpec(Path(os.path.expanduser(item)).resolve()))
        elif isinstance(item, dict):
            path = item.get("path")
            if not path:
                raise SystemExit("[project].repos 的表项必须包含 path")
            specs.append(RepoSpec(
                path=Path(os.path.expanduser(path)).resolve(),
                base=item.get("base") or None,
                head=str(item.get("head") or "HEAD"),
            ))
        else:
            raise SystemExit("[project].repos 只能是路径字符串或 { path, base, head } 表")
    workdir = Path(os.path.expanduser(pc.get("workdir") or pc.get("path") or specs[0].path)).resolve()
    docs_dir = Path(os.path.expanduser(pc.get("docs_dir") or workdir / "docs")).resolve()
    return workdir, specs, docs_dir


def resolve_commit(repo, rev, label):
    try:
        return git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").strip()
    except subprocess.CalledProcessError:
        raise SystemExit(f"{repo} 的 {label}={rev!r} 不是有效提交")


def is_ancestor(repo, ancestor, descendant):
    return subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant],
                          cwd=repo, capture_output=True).returncode == 0


def has_review_content(spec, docs_dir):
    return bool(committed_diff(spec, docs_dir).strip()
                or worktree_diff(spec, docs_dir).strip()
                or untracked_files(spec, docs_dir))


def preflight(workdir, specs, docs_dir, req_id, roles):
    if not workdir.is_dir():
        raise SystemExit(f"工作目录 {workdir} 不存在")
    for spec in specs:
        repo = spec.path
        if not (repo / ".git").exists():
            raise SystemExit(f"{repo} 不是 git 仓库")
        head_sha = resolve_commit(repo, spec.head, "head")
        if spec.base:
            base_sha = resolve_commit(repo, spec.base, "base")
            if not is_ancestor(repo, base_sha, head_sha):
                raise SystemExit(f"{repo} 的 base={spec.base} 不是 head={spec.head} 的祖先，区间方向不对")
        # 修复要落在当前工作区，评审终点必须是当前 HEAD 的祖先（或就是 HEAD）。
        current = git(repo, "rev-parse", "HEAD").strip()
        if not is_ancestor(repo, head_sha, current):
            raise SystemExit(
                f"{repo} 的 head={spec.head} 不在当前检出提交 {current[:12]} 的历史上。"
                "评审终点必须是当前工作区的祖先；如需评审其他分支，请先建 worktree 或在对应分支运行。")
    for kind in ("request", "design", "implement"):
        if not find_doc(docs_dir, kind, req_id):
            raise SystemExit(f"缺少 {docs_dir}/{kind}-{req_id}-*.md")
    if not any(has_review_content(spec, docs_dir) for spec in specs):
        raise SystemExit("所有仓库在评审范围内（docs 以外）都没有代码改动，无可评审内容")
    log("评审范围：\n" + review_scope_text(specs))
    ping_dir = CACHE_DIR / "ping"
    ping_dir.mkdir(parents=True, exist_ok=True)
    for role in roles.values():
        log(f"连通测试 {role.describe()}")
        result = role.run("只回复两个字母：OK", workdir, ping_dir / f"{role.name}.log", 300, max_turns=1)
        log(f"  返回：{result.strip()[:40]}")


# 程序依赖的约定，始终追加在用户提示词之后，用户提示词里不需要也不应改写这些内容
CONTRACT = {
    "reviewer": """
---
以下为自动迭代程序的固定约定，优先于上文：
1. 当前是第 {round} 轮，无人值守，不要向用户提问，也不要等待确认。
2. 本轮评审的代码范围（以每个仓库的实际 git 结果为准）：
{review_scope}
3. 评审报告必须写到 {review_file}（本轮固定文件名，不要使用其他文件名，也不要改动其他轮次的评审文件）。
4. 问题标题必须使用"### 问题N（严重/高/中/低）: ……"格式，程序依赖该格式统计问题数量。
5. 只写评审报告，不得修改 {docs_dir} 以外的任何文件。
""",
    "fixer": """
---
以下为自动迭代程序的固定约定，优先于上文：
1. 当前是第 {round} 轮，无人值守，不要向用户提问，也不要等待确认。
2. 本轮评审的代码范围（修复时按该范围定位问题，修复落在当前工作区）：
{review_scope}
3. 本轮只处理评审文档 {review_file}，处理结果更新到实施文档 {implement_file}。
4. 修复改动保持未提交状态，不要 commit；评审范围里已提交的改动保持原样。
5. 回复的最后一行只能是以下二者之一：
   - LOOP_STATUS: OK（所有确认成立的问题都已修复，且上文要求的验证都已通过）
   - LOOP_STATUS: NEED_HUMAN（存在需要用户确认的事项、无法修复的问题或验证无法通过）
""",
    "techdoc": """
---
以下为自动迭代程序的固定约定：当前无人值守，不要向用户提问；技术文档写到 {docs_dir}。
""",
}


def build_template(name, role_cfg, default_file):
    """用户提示词（prompt_text 或 prompt 文件）+ extra_prompt + 程序固定约定。"""
    if role_cfg.get("prompt_text"):
        text = role_cfg["prompt_text"]
    else:
        path = Path(os.path.expanduser(role_cfg.get("prompt", default_file)))
        text = (path if path.is_absolute() else TOOL_DIR / path).read_text(encoding="utf-8")
    if role_cfg.get("extra_prompt"):
        text = text.rstrip() + "\n\n补充要求：\n" + role_cfg["extra_prompt"].strip() + "\n"
    return text.rstrip() + "\n" + CONTRACT[name]


def render(template, values):
    for k, v in values.items():
        template = template.replace("{" + k + "}", str(v))
    return template


def last_review_round(docs_dir, req_id):
    """已有评审文件中最大的轮次号，新一次运行接着编号，不覆盖历史评审。"""
    rounds = [int(m.group(1)) for f in docs_dir.glob(f"review-{req_id}-*-round*.md")
              if (m := re.search(r"-round(\d+)\.md$", f.name))]
    return max(rounds, default=0)


def main():
    ap = argparse.ArgumentParser(description="评审-修复自动迭代")
    ap.add_argument("-c", "--config", default=str(TOOL_DIR / "config.toml"))
    ap.add_argument("--check", action="store_true", help="只做准备检查与连通测试")
    args = ap.parse_args()

    cfg = tomllib.loads(Path(args.config).read_text(encoding="utf-8"))
    loop_cfg = cfg.get("loop", {})
    workdir, specs, docs_dir = load_project(cfg)
    req_id = cfg["project"]["req_id"]
    skills_dir = cfg.get("skills", {}).get("dir", str(DEFAULT_SKILLS_DIR))
    timeout_s = int(loop_cfg.get("timeout_minutes", 60)) * 60
    role_names = ["reviewer", "fixer"]
    techdoc_role = loop_cfg.get("techdoc_role", "fixer")
    if loop_cfg.get("run_techdoc", False) and techdoc_role not in role_names:
        role_names.append(techdoc_role)
    proxy_env = None
    if any(cfg[n].get("proxy", False) for n in role_names):
        proxy_env = setup_proxy(cfg.get("proxy", {}))
    add_dirs = [d for d in dict.fromkeys([s.path for s in specs] + [docs_dir]) if d != workdir]
    roles = {n: Role(n, cfg[n], add_dirs, skills_dir, proxy_env if cfg[n].get("proxy", False) else None)
             for n in role_names}
    templates = {"reviewer": build_template("reviewer", cfg["reviewer"], "prompts/review.md"),
                 "fixer": build_template("fixer", cfg["fixer"], "prompts/fixer.md"),
                 "techdoc": build_template("techdoc", cfg.get("techdoc", {}), "prompts/techdoc.md")}

    preflight(workdir, specs, docs_dir, req_id, roles)
    if args.check:
        log("准备检查通过")
        return

    run_dir = CACHE_DIR / "runs" / f"{workdir.name}-{req_id}" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}"
    run_dir.mkdir(parents=True)
    log(f"运行目录：{run_dir}")
    base = "；".join(
        f"{s.path.name} {git(s.path, 'rev-parse', '--abbrev-ref', 'HEAD').strip()}"
        f"@{git(s.path, 'rev-parse', '--short', 'HEAD').strip()}"
        + (f" 对比 {s.base}..{s.head}" if s.base else "")
        for s in specs)
    common = {"req_id": req_id, "project": workdir, "workdir": workdir, "docs_dir": docs_dir,
              "repos": "\n".join(f"- {s.path}" for s in specs),
              "review_scope": review_scope_text(specs)}
    history, best, stall, outcome = [], None, 0, ""
    block_levels = loop_cfg.get("block_levels", ["严重", "高"])
    if not block_levels or set(block_levels) - set(LEVELS):
        raise SystemExit(f"block_levels 只能取 {LEVELS} 中的值")
    block_name = "、".join(block_levels)
    round_base = last_review_round(docs_dir, req_id)

    try:
        for rnd in range(1, int(loop_cfg.get("max_rounds", 5)) + 1):
            no = round_base + rnd
            rdir = run_dir / f"round-{no:02d}"
            rdir.mkdir()
            implement = find_doc(docs_dir, "implement", req_id)
            review = docs_dir / f"review-{req_id}-{dt.date.today():%Y%m%d}-round{no:02d}.md"
            values = {**common, "round": no, "implement_file": implement, "review_file": review}

            # 评审：每次都是全新进程、全新对话，结束后不保留会话
            log(f"第 {no} 轮评审开始（{roles['reviewer'].engine}）")
            before_fp = code_fingerprint(specs, docs_dir)
            started = dt.datetime.now().timestamp()
            roles["reviewer"].run(render(templates["reviewer"], values), workdir, rdir / "review.log", timeout_s)
            if code_fingerprint(specs, docs_dir) != before_fp:
                (rdir / "reviewer-changes.patch").write_text(all_diffs(specs), encoding="utf-8")
                raise LoopStop("评审过程修改了 docs 以外的代码，已停止，改动见 reviewer-changes.patch")
            if not review.exists() or review.stat().st_mtime < started:
                raise LoopStop(f"本轮评审没有生成 {review.name}")
            shutil.copy2(review, rdir / review.name)
            counts = count_issues(review.read_text(encoding="utf-8"))
            history.append((review.name, counts))
            blocking = sum(counts[k] for k in block_levels)
            log(f"第 {no} 轮评审结果：{fmt_counts(counts)}（{review.name}）")
            if sum(counts.values()) == 0:
                outcome = f"通过：第 {no} 轮评审没有任何问题"
                break
            # 只剩非阻塞级别的问题：本轮照常修复，修完直接结束，不再开新一轮评审
            final = blocking == 0
            if not final and best is not None and blocking >= best:
                stall += 1
                if stall >= int(loop_cfg.get("no_progress_max", 2)):
                    raise LoopStop(f"连续 {stall} 轮{block_name}级别问题数量没有下降")
            elif not final:
                best, stall = blocking, 0

            # 修复
            log(f"第 {no} 轮修复开始（{roles['fixer'].engine}）")
            result = roles["fixer"].run(render(templates["fixer"], values), workdir, rdir / "fix.log", timeout_s)
            (rdir / "changes.patch").write_text(all_diffs(specs), encoding="utf-8")
            implement = find_doc(docs_dir, "implement", req_id)
            shutil.copy2(implement, rdir / implement.name)
            status = STATUS_RE.findall(result)
            if not status:
                raise LoopStop("修复进程没有输出 LOOP_STATUS，见 fix.log")
            if status[-1] == "NEED_HUMAN":
                raise LoopStop("修复进程报告存在需要人工确认的事项，见实施文档待确认事项")
            if final:
                outcome = (f"通过：第 {no} 轮评审没有{block_name}级别问题，"
                           f"剩余问题已在本轮修复，不再进行新一轮评审")
                break
        else:
            outcome = f"停止：达到最大轮数仍有{block_name}级别问题"
    except LoopStop as e:
        outcome = f"停止：{e}"

    if outcome.startswith("通过") and loop_cfg.get("run_techdoc", False):
        log("生成最终技术文档")
        try:
            roles[techdoc_role].run(render(templates["techdoc"], common), workdir,
                                    run_dir / "techdoc.log", timeout_s)
            doc = find_doc(docs_dir, "techdoc", req_id)
            outcome += f"；技术文档：{doc if doc else '未找到生成的文件'}"
        except LoopStop as e:
            outcome += f"；技术文档生成失败：{e}"

    lines = [f"# 评审修复迭代汇总：{req_id}", "", f"- 工作目录：{workdir}", f"- 文档目录：{docs_dir}",
             f"- 起始状态：{base}", f"- {roles['reviewer'].describe()}", f"- {roles['fixer'].describe()}",
             f"- 结论：{outcome}", "",
             "| 轮次 | 评审文件 | 严重 | 高 | 中 | 低 |", "|------|----------|------|----|----|----|"]
    lines += [f"| {round_base + i} | {name} | " + " | ".join(str(c[k]) for k in LEVELS) + " |"
              for i, (name, c) in enumerate(history, 1)]
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(outcome)
    log(f"汇总：{run_dir / 'summary.md'}")
    sys.exit(0 if outcome.startswith("通过") else 1)


if __name__ == "__main__":
    main()
