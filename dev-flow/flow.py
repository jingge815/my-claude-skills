#!/usr/bin/env python3
"""dev-flow 一键全流程确定性执行器。

读取任务配置（tasks/<需求标识>.toml，样例见 tasks/example.toml），按固定流水线在
独立进程中驱动外部模型完成：W0 任务 worktree → S0 prompt 锐化 → S1 需求文档 →
S2 技术设计（single = 单模型 + N 轮自评审修复；arena = 多候选并行 + 裁判选一 +
移植合成）→ S3 TDD 实施 → S4+S5 评审修复迭代与技术文档（生成任务级配置调用
review-fix-loop/loop.py）。角色（厂商账号）来自本目录 config.toml，任务参数全部
在任务配置中，多任务各一份配置即可真正并行。外部调用复用 loop.py 的引擎层。

用法：
    python3 flow.py -c tasks/xxx.toml --check      # 校验配置/仓库/角色，不调模型、不建 worktree
    python3 flow.py -c tasks/xxx.toml --ping       # --check 基础上对用到的角色做连通测试
    python3 flow.py -c tasks/xxx.toml              # 全流程运行
    python3 flow.py -c tasks/xxx.toml --resume     # 从首个未完成阶段继续（沿用产物与 run 状态）
    python3 flow.py -c tasks/xxx.toml --stage S3   # 只跑指定阶段，逗号分隔（W0 S0 S1 S2 S3 S4 S5）
    python3 flow.py status <需求标识> [--watch]    # 查看最近一次运行的实时状态（--watch 持续输出）

产出：docs 目录下 request/design/implement/review-*/techdoc 文档、flow-state 状态文件、
decisions.tsv 决策日志；运行目录 ~/.cache/devflow/runs/<需求标识>/<时间>/ 保存心跳、
各阶段调用日志、生成的 loop 配置与看门狗报告。
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
SKILLS_DIR = TOOL_DIR.parent
LOOP_DIR = SKILLS_DIR / "review-fix-loop"
sys.path.insert(0, str(LOOP_DIR))
sys.path.insert(0, str(TOOL_DIR))

from loop import LoopStop, find_doc, git, log, log_decision  # noqa: E402  复用评审-修复循环的引擎层
import agents  # noqa: E402  全局角色配置与 build_role

CACHE_ROOT = Path.home() / ".cache" / "devflow" / "runs"
STAGES = ["W0", "S0", "S1", "S2", "S3", "S4"]
STATUS_RE = re.compile(r"FLOW_STATUS:\s*(OK|NEED_HUMAN)")
DEFAULT_FP_EXCLUDE = ["test-results/", "__pycache__/", "*.log", "runs/", "results/", "build/", ".pytest_cache/"]

# S0-S3 各阶段提示词末尾的固定约定（S4/S5 由 loop.py 追加它自己的约定）
CONTRACT_FLOW = """
---
以下为流程程序的固定约定，优先于上文：
1. 无人值守：不要向用户提问、不要等待确认。需要用户裁决的事项写入指定文档的“待确认事项”，
   按最接近核心目标的保守解释继续，不要自行扩大或缩小范围。
2. 产出必须写到指定路径（不要另起文件名，不要改动其他需求的文档）。
3. 回复的最后一行只能是：FLOW_STATUS: OK（产出完成）或 FLOW_STATUS: NEED_HUMAN（存在无法绕开、必须用户拍板的事项）。
"""


class FlowStop(Exception):
    """阶段失败或需要停下来交给人处理的情况。"""


def expand(p):
    return Path(os.path.expanduser(str(p)))


class Repo:
    """任务配置里的一个仓库：源路径 + 角色说明 + 短名（worktree 目录名）。"""

    def __init__(self, item, workdir):
        if isinstance(item, str):
            path, role = item, ""
        else:
            path, role = item.get("path", ""), item.get("role", "")
        p = expand(path)
        self.src = (workdir / p).resolve() if not p.is_absolute() else p.resolve()
        self.role = str(role)
        self.name = self.src.name


class Task:
    """任务配置（tasks/<需求标识>.toml）的解析结果。"""

    def __init__(self, cfg_path):
        self.cfg_path = Path(cfg_path).resolve()
        if not self.cfg_path.exists():
            raise SystemExit(f"任务配置不存在：{self.cfg_path}")
        raw = tomllib.loads(self.cfg_path.read_text(encoding="utf-8"))
        t, p = raw.get("task", {}), raw.get("project", {})

        self.req_id = str(t.get("req_id") or self.cfg_path.stem)
        self.date = str(t.get("date") or dt.date.today().strftime("%Y%m%d"))
        op = t.get("original_prompt", "").strip()
        if not op and t.get("prompt_file"):
            op = expand(t["prompt_file"]).read_text(encoding="utf-8")
        self.original_prompt = op.strip()
        if not self.original_prompt:
            raise SystemExit("task.original_prompt（或 prompt_file）必须提供原始需求描述")

        self.workdir = expand(p.get("workdir", ".")).resolve()
        items = p.get("repos") or []
        if isinstance(items, (str, dict)):
            items = [items]
        self.repos = [Repo(i, self.workdir) for i in items]
        if not self.repos:
            raise SystemExit("[project].repos 必须列出至少一个仓库")
        names = [r.name for r in self.repos]
        for i, r in enumerate(self.repos):  # 短名撞车时用序号区分，保证 worktree 不重叠
            if names.count(r.name) > 1:
                r.name = f"{r.name}-{i}"
        self.readonly = [str(expand(x)) for x in p.get("readonly_paths", [])]
        self.reference_docs = [str(x) for x in p.get("reference_docs", [])]
        self.use_worktree = bool(p.get("use_worktree", True))
        self.worktree_parent = expand(p.get("worktree_parent") or self.workdir / "worktrees").resolve()
        docs_repo = p.get("docs_repo") or self.repos[0].name
        self.docs_repo = next((r for r in self.repos if r.name == docs_repo), self.repos[0])
        self.docs_dir_override = p.get("docs_dir", "")
        self.fingerprint_exclude = p.get("fingerprint_exclude") or DEFAULT_FP_EXCLUDE
        self._docs_dir_guess = self.workdir  # resolve_paths 后更新为真实 docs 目录

        g = raw.get("goals", {})
        self.core_goals = str(g.get("core_goals", "")).strip()
        self.verify_commands = str(g.get("verify_commands", "")).strip()
        self.boundaries = str(g.get("boundaries", "")).strip()

        r = raw.get("roles", {})
        self.roles = {
            "sharpen": r.get("sharpen", "leader"),
            "requirement": r.get("requirement", "worker:doc"),
            "design": r.get("design", "worker:doc"),
            "design_review": r.get("design_review", "") or r.get("design", "worker:doc"),
            "design_judge": r.get("design_judge", "leader"),
            "design_graft": r.get("design_graft", "") or r.get("design", "worker:doc"),
            "tdd": r.get("tdd", "worker:code"),
            "techdoc": r.get("techdoc", "worker:doc"),
        }
        d = raw.get("design", {})
        self.design_mode_cfg = str(d.get("mode", "auto"))
        self.self_review_rounds = int(d.get("self_review_rounds", 3))
        self.s4 = raw.get("s4_loop", {})
        m = raw.get("monitor", {})
        self.stage_timeout_min = int(m.get("stage_timeout_minutes", 240))
        self.total_timeout_hours = float(m.get("total_timeout_hours", 24))
        self.watchdog = bool(m.get("watchdog", True))
        self.prompts = raw.get("prompts", {})
        self.existing = {"request": t.get("request_file", ""), "design": t.get("design_file", "")}


class Paths:
    """W0 解析后的实际路径：worktree 模式下全部指向任务 worktree。"""

    def __init__(self, repos, workdir, docs_dir):
        self.repos = repos          # name -> Path
        self.workdir = workdir
        self.docs_dir = docs_dir
        self.request_file = docs_dir / "request.md"
        self.design_file = docs_dir / "design.md"
        self.implement_file = docs_dir / "implement.md"
        self.progress_file = docs_dir / "progress.md"


def hb(run_dir, stage, step):
    """心跳：看门狗与 status 据此判断流程是否还在推进。"""
    (run_dir / "heartbeat.json").write_text(
        json.dumps({"ts": time.time(), "iso": dt.datetime.now().isoformat(timespec="seconds"),
                    "stage": stage, "step": step, "pid": os.getpid()}, ensure_ascii=False),
        encoding="utf-8")


def log_progress(P, stage, step, progress, evidence=""):
    """工作进展日志（append-only，show-me-your-work 纪律）：执行到哪一步、当前进展、证据指针。

    决策点（选型、门禁、偏差）的 why 记录在 decisions.tsv，这里记步骤级进展；
    证据一律是路径/文件名，不写散文。
    """
    path = P.progress_file
    if not path.exists():
        path.write_text(f"# 工作进展：{P.docs_dir}\n\n按时间追加；决策与理由见 decisions.tsv；"
                        "当前快照见 flow-state。证据标注「本次实际验证/复用/未执行」由各文档自行记录。\n\n",
                        encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"## {dt.datetime.now():%Y-%m-%d %H:%M:%S}｜{stage}｜{step}\n")
        f.write(f"- 进展：{progress}\n")
        if evidence:
            f.write(f"- 证据：{evidence}\n")
        f.write("\n")


def resolve_refdoc(task, ref):
    """借鉴文档路径：相对路径依次按 docs 目录、docs 仓源路径、workdir 解析。"""
    p = expand(ref)
    if p.is_absolute():
        return str(p)
    for base in (task._docs_dir_guess, task.docs_repo.src, task.workdir):
        cand = base / p
        if cand.exists():
            return str(cand)
    return ref


def add_dirs_for(task, P):
    """外部进程需要 --add-dir 额外授权的目录。"""
    dirs = [str(p) for p in P.repos.values()] + [str(P.docs_dir)] + task.readonly
    return [d for d in dict.fromkeys(dirs) if d != str(P.workdir)]


def load_prompt(task, name):
    """提示词模板：prompts/<name>.md，任务配置可用 <name>_text / <name> 覆盖。"""
    o = task.prompts
    if o.get(f"{name}_text"):
        return str(o[f"{name}_text"])
    path = expand(o.get(name, f"prompts/{name}.md"))
    if not path.is_absolute():
        path = TOOL_DIR / path
    if not path.exists():
        raise FlowStop(f"提示词模板不存在：{path}")
    return path.read_text(encoding="utf-8")


def build_prompt(task, name, **vals):
    """模板 + 任务配置补充段 + 固定约定，再做 token 替换（逐个 replace，内容含花括号也安全）。"""
    text = load_prompt(task, name)
    if task.prompts.get(f"{name}_extra"):
        text = text.rstrip() + "\n\n补充要求：\n" + str(task.prompts[f"{name}_extra"]).strip() + "\n"
    if name != "sharpen":
        text = text.rstrip() + "\n" + CONTRACT_FLOW
    for k, v in vals.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def common_values(task, P):
    repos_block = "\n".join(f"- {p}（{r.role}）" if r.role else f"- {p}"
                            for r, p in ((r, P.repos[r.name]) for r in task.repos))
    readonly_block = "\n".join(f"- {x}" for x in task.readonly) or "无"
    refdocs_block = "\n".join(f"- {resolve_refdoc(task, x)}" for x in task.reference_docs) or "无"
    return {
        "req_id": task.req_id, "date": task.date, "workdir": P.workdir, "docs_dir": P.docs_dir,
        "repos_block": repos_block, "readonly_block": readonly_block, "refdocs_block": refdocs_block,
        "core_goals": task.core_goals or "（见需求文档核心目标一节）",
        "verify_commands": task.verify_commands or "（见需求/设计文档验证方案）",
        "boundaries": task.boundaries or "无",
        "original_prompt": task.original_prompt,
        "request_file": P.request_file, "design_file": P.design_file,
        "implement_file": P.implement_file,
    }


def call_role(gcfg, task, P, run_dir, role_key, prompt, stage, step, timeout_min=None):
    """调用一个全局角色执行一次全新对话；心跳前后各更新一次。"""
    hb(run_dir, stage, step)
    name, role = agents.build_role(gcfg, task.roles[role_key], add_dirs_for(task, P))
    log(f"[{stage}] {step}（角色 {name}）")
    safe = re.sub(r"[^0-9A-Za-z_-]+", "-", step)[:60]
    log_file = run_dir / f"{stage}-{safe}.log"
    try:
        result = role.run(prompt, str(P.workdir), log_file, (timeout_min or task.stage_timeout_min) * 60, None)
    except LoopStop as e:
        raise FlowStop(f"[{stage}] {step} 调用失败：{e}")
    hb(run_dir, stage, step + " 完成")
    log_progress(P, stage, step, f"外部调用完成（角色 {name}）", f"调用日志 {log_file}")
    return result


def check_status(result, stage):
    """解析外部回复末行的 FLOW_STATUS；NEED_HUMAN 即安全暂停。"""
    found = STATUS_RE.findall(result)
    if found and found[-1] == "NEED_HUMAN":
        raise FlowStop(f"[{stage}] 角色报告 NEED_HUMAN：存在必须用户拍板的事项，见对应文档的待确认事项")
    if not found:
        log(f"[{stage}] 警告：回复未输出 FLOW_STATUS，按产出文件判断")


# ---------------------------------------------------------------- W0 worktree

def resolve_paths(task, run_dir, state):
    """W0：worktree 模式下为每个仓库建/复用任务 worktree，确定 docs 目录与文档命名。"""
    if task.use_worktree:
        root = task.worktree_parent / task.req_id
        root.mkdir(parents=True, exist_ok=True)
        paths = {}
        for r in task.repos:
            wt = root / r.name
            if wt.exists():
                log(f"W0 复用 worktree：{wt}")
            else:
                branch = f"dev-flow/{task.req_id}"
                add = subprocess.run(["git", "worktree", "add", "-b", branch, str(wt)],
                                     cwd=r.src, capture_output=True, text=True)
                if add.returncode != 0:
                    # 分支已存在（上次运行建过）时直接挂回
                    add = subprocess.run(["git", "worktree", "add", str(wt), branch],
                                         cwd=r.src, capture_output=True, text=True)
                if add.returncode != 0:
                    raise FlowStop(f"W0 为 {r.src} 创建 worktree 失败：{add.stderr.strip()[:200]}")
                log(f"W0 新建 worktree：{wt}（分支 {branch}）")
            paths[r.name] = wt
        workdir = root
    else:
        paths = {r.name: r.src for r in task.repos}
        workdir = task.workdir

    docs_repo_path = paths[task.docs_repo.name]
    docs_dir = expand(task.docs_dir_override).resolve() if task.docs_dir_override else docs_repo_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    task._docs_dir_guess = docs_dir
    P = Paths(paths, workdir, docs_dir)

    # 已有文档时沿用其日期命名，保证 --resume / --stage 补跑前后文件名一致
    for kind, attr in (("request", "request_file"), ("design", "design_file"), ("implement", "implement_file")):
        found = find_doc(docs_dir, kind, task.req_id)
        if found:
            m = re.search(r"-(\d{8})\.md$", found.name)
            if m:
                task.date = m.group(1)
            setattr(P, attr, found)
            continue
        setattr(P, attr, docs_dir / f"{kind}-{task.req_id}-{task.date}.md")
    # 配置里显式指定的已有文档优先
    for kind, key in (("request", "request"), ("design", "design")):
        if task.existing.get(kind):
            f = expand(task.existing[kind])
            setattr(P, f"{kind}_file", f)
    P.progress_file = docs_dir / f"progress-{task.req_id}-{task.date}.md"

    state["worktrees"] = {}
    for r in task.repos:
        p = paths[r.name]
        branch = git(p, "rev-parse", "--abbrev-ref", "HEAD").strip()
        sha = git(p, "rev-parse", "--short", "HEAD").strip()
        state["worktrees"][r.name] = f"{branch}@{sha}"
    log_decision(docs_dir, "W0", "任务 worktree 就绪", "每仓一个任务级 worktree，子任务不建",
                 "; ".join(f"{k}:{v}" for k, v in state["worktrees"].items()), "ok")
    log_progress(P, "W0", "worktree 就绪",
                 "启用" if task.use_worktree else "直连原仓（未建 worktree）",
                 "基线见 flow-state 仓库基线表与 decisions.tsv 的 W0 行")
    hb(run_dir, "W0", "worktree 就绪")
    return P


# ---------------------------------------------------------------- S0-S3 各阶段

def stage_s0(gcfg, task, P, run_dir):
    prompt = build_prompt(task, "sharpen", original_prompt=task.original_prompt)
    out = call_role(gcfg, task, P, run_dir, "sharpen", prompt, "S0", "prompt 锐化")
    sharpened = out.strip()
    if STATUS_RE.search(sharpened):
        sharpened = STATUS_RE.sub("", sharpened).strip()
    if not sharpened:
        raise FlowStop("S0 锐化输出为空")
    (run_dir / "sharpened.md").write_text(sharpened, encoding="utf-8")
    log_decision(P.docs_dir, "S0", "prompt 锐化完成", "更深而非更大", run_dir / "sharpened.md", "ok")
    return sharpened


def gate_doc(path, must_have, stage, what):
    if not path.exists():
        raise FlowStop(f"{stage} 未生成{what}：{path}")
    text = path.read_text(encoding="utf-8")
    missing = [k for k in must_have if k not in text]
    if missing:
        raise FlowStop(f"{stage} {path.name} 缺少必备小节：{'、'.join(missing)}")
    return text


def stage_s1(gcfg, task, P, run_dir, sharpened):
    if P.request_file.exists():
        log(f"S1 需求文档已存在（{P.request_file.name}），跳过")
        return
    vals = dict(common_values(task, P), sharpened=sharpened or "（本次未锐化）")
    res = call_role(gcfg, task, P, run_dir, "requirement",
                    build_prompt(task, "requirement", **vals), "S1", "需求文档 requirement-doc")
    check_status(res, "S1")
    gate_doc(P.request_file, ["核心目标", "验收"], "S1", "需求文档")
    log_decision(P.docs_dir, "S1", "需求文档门禁通过", "含核心目标与验收标准", P.request_file.name, "ok")
    log_progress(P, "S1", "门禁通过", f"需求文档含核心目标与验收标准：{P.request_file.name}",
                 f"{P.request_file}；调用日志 {run_dir}/S1-*.log")


def resolve_design_mode(task, gcfg):
    if task.design_mode_cfg not in ("auto", "single", "arena"):
        raise FlowStop(f"[design].mode 只能是 auto/single/arena，当前 {task.design_mode_cfg}")
    if task.design_mode_cfg != "auto":
        return task.design_mode_cfg
    entries = (gcfg.get("panel") or {}).get("arena") or []
    mode = "arena" if len(entries) >= 2 else "single"
    return mode


def stage_s2(gcfg, task, P, run_dir, state):
    if P.design_file.exists():
        log(f"S2 设计文档已存在（{P.design_file.name}），跳过")
        return
    mode = resolve_design_mode(task, gcfg)
    state["design_mode"] = mode
    log_decision(P.docs_dir, "S2", f"设计模式：{mode}",
                 "auto = panel.arena 候选数决定" if task.design_mode_cfg == "auto" else "任务配置指定",
                 f"panel.arena 条目 {len((gcfg.get('panel') or {}).get('arena') or [])}", mode)
    log_progress(P, "S2", "设计模式判定", f"{mode}", "decisions.tsv 的 S2 设计模式行")
    vals = common_values(task, P)
    if mode == "arena":
        _s2_arena(gcfg, task, P, run_dir, vals)
    else:
        _s2_single(gcfg, task, P, run_dir, vals)
    gate_doc(P.design_file, ["任务"], "S2", "设计文档")
    log_decision(P.docs_dir, "S2", "设计文档门禁通过", "含任务拆分（DAG 检查由评审阶段把关）",
                 P.design_file.name, "ok")
    log_progress(P, "S2", "门禁通过", f"设计文档产出：{P.design_file.name}",
                 f"{P.design_file}；arena 产物见 {P.docs_dir}/arena/{task.req_id}/（如有）")


def _s2_single(gcfg, task, P, run_dir, vals):
    res = call_role(gcfg, task, P, run_dir, "design",
                    build_prompt(task, "design", **vals), "S2", "设计初稿 tech-design-doc")
    check_status(res, "S2")
    gate_doc(P.design_file, ["核心目标"], "S2", "设计文档")
    n = task.self_review_rounds
    for i in range(1, n + 1):
        step = f"自评审第 {i}/{n} 轮"
        started = time.time()
        res = call_role(gcfg, task, P, run_dir, "design_review",
                        build_prompt(task, "design-review", **vals, round=i, rounds=n), "S2", step)
        check_status(res, "S2")
        text = P.design_file.read_text(encoding="utf-8")
        updated = f"第 {i} 轮" in text or f"第{i}轮" in text or P.design_file.stat().st_mtime > started
        if not updated:
            raise FlowStop(f"S2 {step} 未更新设计文档（无自评审记录且文件未修改），见 {run_dir}")
        log_decision(P.docs_dir, "S2", f"设计自评审第 {i} 轮完成", "重读代码+对照核心目标+就地修复",
                     P.design_file.name, "ok")
        log_progress(P, "S2", f"自评审第 {i}/{n} 轮完成", "设计文档已就地更新并追加自评审记录",
                     f"{P.design_file.name} 末尾自评审记录；调用日志 {run_dir}/S2-*.log")


def _s2_arena(gcfg, task, P, run_dir, vals):
    entries = (gcfg.get("panel") or {}).get("arena") or []
    if len(entries) < 2:
        raise FlowStop("arena 模式需要全局 config.toml 中至少 2 个 [[panel.arena]] 条目")
    cand_dir = P.docs_dir / "arena" / task.req_id
    cand_dir.mkdir(parents=True, exist_ok=True)
    log(f"S2 arena：{len(entries)} 个候选并行（in_session 条目在 flow.py 中同样按外部进程执行）")

    def launch(i, ent):
        name = str(ent.get("name", i))
        cfile = cand_dir / f"candidate-{i + 1:02d}-{name}.md"
        prompt = build_prompt(task, "design-arena", **vals, candidate_name=name, candidate_file=cfile)
        _, role = agents.build_role(gcfg, f"arena:{name}", add_dirs_for(task, P))
        return name, cfile, role.run(prompt, str(P.workdir), run_dir / f"S2-candidate-{name}.log",
                                     task.stage_timeout_min * 60, None)

    files, dropouts = [], []
    with ThreadPoolExecutor(max_workers=len(entries)) as ex:
        futs = {ex.submit(launch, i, ent): ent for i, ent in enumerate(entries)}
        for fut in futs:
            try:
                name, cfile, _ = fut.result()
                if cfile.exists():
                    files.append(cfile)
                else:
                    dropouts.append(f"{name}（未产出文件）")
            except Exception as e:  # 单个候选失败不拖垮整场，记掉队继续
                dropouts.append(f"{ent_name(futs[fut])}（{str(e)[:80]}）")
    if dropouts:
        log(f"S2 arena 落选进程：{'；'.join(dropouts)}")
        log_decision(P.docs_dir, "S2", "arena 候选掉队", "继续用剩余候选", "; ".join(dropouts), "继续")
    if not files:
        raise FlowStop("S2 arena 没有任何候选产出设计文档")

    candidates_block = "\n".join(f"- {f}" for f in files)
    select_file = cand_dir / "select.md"
    res = call_role(gcfg, task, P, run_dir, "design_judge",
                    build_prompt(task, "design-select", **vals, candidates_block=candidates_block,
                                 select_file=select_file), "S2", "arena 裁判选型")
    check_status(res, "S2")
    if not select_file.exists():
        raise FlowStop(f"S2 裁判未产出选型报告：{select_file}")
    log_decision(P.docs_dir, "S2", "arena 裁判选型完成", "多候选按评分标准选 base + 移植清单",
                 select_file.name, "ok")

    res = call_role(gcfg, task, P, run_dir, "design_graft",
                    build_prompt(task, "design-graft", **vals, select_file=select_file,
                                 candidates_block=candidates_block), "S2", "arena 移植合成")
    check_status(res, "S2")


def ent_name(ent):
    return str(ent.get("name", "?"))


def stage_s3(gcfg, task, P, run_dir):
    if P.implement_file.exists():
        log(f"S3 实施文档已存在（{P.implement_file.name}），跳过")
        return
    req = find_doc(P.docs_dir, "request", task.req_id)
    des = find_doc(P.docs_dir, "design", task.req_id)
    if not (req and des):
        raise FlowStop("S3 缺少需求或设计文档，先完成 S1/S2")
    vals = dict(common_values(task, P), request_file=req, design_file=des)
    res = call_role(gcfg, task, P, run_dir, "tdd",
                    build_prompt(task, "tdd", **vals), "S3", "TDD 实施 tdd-implement")
    check_status(res, "S3")
    if not P.implement_file.exists():
        raise FlowStop(f"S3 未生成实施文档：{P.implement_file}")
    log_decision(P.docs_dir, "S3", "TDD 实施完成", "先测后码，任务单元全覆盖",
                 P.implement_file.name, "ok")
    log_progress(P, "S3", "实施完成", f"实施文档与代码改动产出：{P.implement_file.name}",
                 f"{P.implement_file}（验证命令与结果、偏差说明在其内）；各仓 git 未提交改动；"
                 f"调用日志 {run_dir}/S3-*.log")


# ---------------------------------------------------------------- S4+S5 生成 loop 配置

def toml_val(v):
    """把标量/列表/内联表写成 TOML 字面量（字符串借 json 转义，两者兼容）。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(toml_val(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {toml_val(str(x))}" for k, x in v.items()) + " }"
    raise FlowStop(f"无法写成 TOML 的值：{v!r}")


def toml_ml(s):
    """多行字符串优先用字面量形式（不转义）；含 ''' 时退回基本字符串。"""
    s = s.strip("\n")
    if "'''" not in s:
        return "'''\n" + s + "\n'''"
    return '"""\n' + s.replace("\\", "\\\\").replace('"', '\\"') + '\n"""'


def role_lines(header, rcfg, default_prompt, extra):
    out = [f"[{header}]"]
    for k in ("engine", "model", "settings", "settings_file", "base_url", "auth_token"):
        if rcfg.get(k):
            out.append(f"{k} = {toml_val(str(rcfg[k]))}")
    if rcfg.get("args"):
        out.append(f"args = {toml_val([str(a) for a in rcfg['args']])}")
    if rcfg.get("env"):
        out.append(f"env = {toml_val({k: str(v) for k, v in rcfg['env'].items()})}")
    out.append(f"prompt = {toml_val(default_prompt)}")
    if extra:
        out.append(f"extra_prompt = {toml_ml(extra)}")
    return out


def gen_loop_config(task, P, run_dir, gcfg):
    """把任务配置展开成 review-fix-loop 的任务级配置，写入 run 目录（不动全局 config.toml）。"""
    s4 = task.s4
    reviewer = s4.get("reviewer") or gcfg.get("leader") or {}
    fixer = s4.get("fixer") or (gcfg.get("worker") or {}).get("code") or {}
    if not reviewer or not fixer:
        raise FlowStop("S4 角色缺失：任务配置 [s4_loop].reviewer/fixer 或全局 leader/worker.code 至少一组")

    req = find_doc(P.docs_dir, "request", task.req_id) or P.request_file
    des = find_doc(P.docs_dir, "design", task.req_id) or P.design_file
    repos = "\n".join(f"- {P.repos[r.name]}（{r.role}）" if r.role else f"- {P.repos[r.name]}"
                      for r in task.repos)
    readonly = "\n".join(f"- {x}" for x in task.readonly) or "无"
    goals = task.core_goals or "（见需求文档核心目标一节）"
    verify = task.verify_commands or "（见需求/设计文档验证方案，只做最小验证）"
    boundary = task.boundaries or "无"

    reviewer_extra = f"""（本段由 dev-flow flow.py 依据任务配置自动生成）
零、评审基准与核心目标
唯一基准：需求文档 {req} 与设计文档 {des}（均在文档目录 {P.docs_dir}）。
实施文档自称的结论须与代码逐条核对，不得直接采信。
围绕以下核心目标逐条回答「是否完成、证据、差距」；未完成的必须写清差距，
这些差距就是后续修复要完成的范围：
{goals}

一、评审对象（各仓 git 未提交改动）
{repos}
只读参考：{readonly}

二、边界（不得列为缺陷）
{boundary}

三、验证纪律（只做确认问题所需的最小验证，禁止小时级长跑）
{verify}"""

    fixer_extra = f"""（本段由 dev-flow flow.py 依据任务配置自动生成）
零、任务
根据本轮评审报告 {{review_file}} 与需求/设计文档修复问题：
- 需求文档：{req}
- 设计文档：{des}
先确认问题真实存在（读代码、跑最小复现，不能只信评审结论）；确认成立的全部修复
（包括中、低级别）；评审报告「核心目标完成度」中未完成或部分完成的条目必须一并覆盖。

一、仓库与修复纪律
{repos}
- 每个仓库先读自己的 CLAUDE.md 并遵守；修复改动保持未提交，不要 commit。
- 需要用户裁决的事项（范围扩张、接口拍板、外部依赖）写入实施文档待确认事项并跳过。

二、验证（只做最小必要验证）
{verify}"""

    techdoc_extra = f"""（本段由 dev-flow flow.py 依据任务配置自动生成）
- 需求文档（核心目标唯一来源）：{req}
- 设计文档：{des}
- 代码改动：以下仓库的 git 未提交改动
{repos}
- 围绕核心目标逐条对照，必须包含「核心目标完成度」矩阵（逐条状态、证据、缺口）：
{goals}"""

    lines = [
        f"# 由 dev-flow flow.py 自动生成（任务配置：{task.cfg_path}），手动修改会被下次生成覆盖",
        "[project]",
        f"req_id = {toml_val(task.req_id)}",
        f"workdir = {toml_val(str(P.workdir))}",
        f"repos = {toml_val([str(p) for p in P.repos.values()])}",
        f"docs_dir = {toml_val(str(P.docs_dir))}",
        f"fingerprint_exclude = {toml_val(task.fingerprint_exclude)}",
        "",
        "[loop]",
        f"max_rounds = {int(s4.get('max_rounds', 20))}",
        f"no_progress_max = {int(s4.get('no_progress_max', 2))}",
        f"block_levels = {toml_val(s4.get('block_levels', ['严重', '高']))}",
        f"timeout_minutes = {int(s4.get('timeout_minutes', task.stage_timeout_min))}",
        f"run_techdoc = {toml_val(bool(s4.get('run_techdoc', True)))}",
        'techdoc_role = "fixer"',
        f"interrogate = {toml_val(bool(s4.get('interrogate', False)))}",
        "",
        "[skills]",
        f"dir = {toml_val(str(SKILLS_DIR))}",
        "",
    ]
    lines += role_lines("reviewer", reviewer, "prompts/review.md", reviewer_extra) + [""]
    lines += role_lines("fixer", fixer, "prompts/fixer.md", fixer_extra) + [""]
    lines += role_lines("techdoc", {}, "prompts/techdoc.md", techdoc_extra) + [""]
    if any(x.get("proxy") for x in (reviewer, fixer) if isinstance(x, dict)):
        log("警告：S4 角色的 proxy 标记不会带进生成的配置；需要代理时在任务配置 [s4_loop.proxy] 写命令")
    proxy = s4.get("proxy")
    if proxy:
        lines += ["[proxy]",
                  f"commands = {toml_val(proxy.get('commands', []))}",
                  f"check_url = {toml_val(proxy.get('check_url', ''))}", ""]

    path = run_dir / "loop-config.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tomllib.loads(path.read_text(encoding="utf-8"))  # 生成即校验，坏配置不进 S4
    return path


def stage_s4(gcfg, task, P, run_dir, state):
    cfg_path = gen_loop_config(task, P, run_dir, gcfg)
    cmd = [sys.executable, "-u", str(LOOP_DIR / "loop.py"), "-c", str(cfg_path)]
    log(f"S4+S5 评审修复迭代启动（配置 {cfg_path.name}，技术文档随通过自动生成）")
    log_progress(P, "S4", "评审修复迭代启动", f"loop.py 使用任务级配置，轮数上限 "
                f"{task.s4.get('max_rounds', 20)}，阻塞级 {'/'.join(task.s4.get('block_levels', ['严重', '高']))}",
                f"{cfg_path}；各轮评审报告将写到 {P.docs_dir}/review-{task.req_id}-*-roundNN.md")
    hb(run_dir, "S4", "loop 启动")
    proc = subprocess.Popen(cmd, cwd=str(P.workdir), stdout=subprocess.PIPE, text=True, bufsize=1,
                            env={**os.environ, "PYTHONUNBUFFERED": "1"})
    notable = re.compile(r"(轮评审结果|轮修复开始|对抗评审|结论：|技术文档|准备检查通过|汇总：)")
    for line in proc.stdout:  # 逐行转发 loop 日志，同时借每轮日志刷新心跳与进展日志
        sys.stdout.write(line)
        sys.stdout.flush()
        hb(run_dir, "S4", line.strip()[:80])
        if notable.search(line):
            log_progress(P, "S4", "loop 事件", line.strip(), f"{P.docs_dir} 下对应轮次文件")
    rc = proc.wait()
    if rc != 0:
        log_progress(P, "S4", "迭代未通过", f"loop.py 退出码 {rc}", f"运行记录见 loop 汇总与 {P.docs_dir} 各轮报告")
        raise FlowStop(f"S4 评审修复迭代未通过（loop.py 退出码 {rc}）；各轮报告见 {P.docs_dir}，"
                       f"运行记录见 run 目录与 loop 汇总")
    techdoc_note = "技术文档已生成" if task.s4.get("run_techdoc", True) else "技术文档未开（run_techdoc=false）"
    log_decision(P.docs_dir, "S4", "评审修复迭代通过", "阻塞级清零后修完剩余问题",
                 f"loop-config={cfg_path.name}", f"ok；{techdoc_note}")
    log_progress(P, "S4", "迭代通过", techdoc_note,
                 f"{P.docs_dir} 各轮 review-*.md 与 implement 文档；技术文档见同目录 techdoc-*.md")


def stage_s5(gcfg, task, P, run_dir):
    """单独补跑技术文档（正常路径由 S4 的 loop run_techdoc 完成，不会走到这里）。"""
    req = find_doc(P.docs_dir, "request", task.req_id)
    des = find_doc(P.docs_dir, "design", task.req_id)
    imp = find_doc(P.docs_dir, "implement", task.req_id)
    if not (req and des):
        raise FlowStop("S5 缺少需求/设计文档")
    vals = dict(common_values(task, P), request_file=req, design_file=des,
                implement_file=imp or P.implement_file)
    res = call_role(gcfg, task, P, run_dir, "techdoc",
                    build_prompt(task, "techdoc", **vals), "S5", "技术文档 techdoc")
    check_status(res, "S5")
    log_decision(P.docs_dir, "S5", "技术文档生成", "交付模式，基于最终代码与过程文档",
                 str(P.docs_dir), "ok")
    log_progress(P, "S5", "技术文档生成", "单独补跑路径完成", f"{P.docs_dir}/techdoc-{task.req_id}-*.md")


# ---------------------------------------------------------------- 状态与看门狗

def write_flow_state(task, P, state, run_dir):
    rows = []
    for st in STAGES + ["S5"]:
        info = state["stages"].get(st)
        if not info:
            continue
        rows.append(f"| {st} | {info['status']} | {info.get('detail', '')} |")
    wt = "\n".join(f"| {k} | {P.repos[k]} | {v} |" for k, v in state.get("worktrees", {}).items())
    text = (
        f"# flow-state：{task.req_id}\n\n"
        f"- 任务配置：{task.cfg_path}\n- 运行目录：{run_dir}\n"
        f"- worktree：{'启用' if task.use_worktree else '直连原仓'}；"
        f"设计模式：{state.get('design_mode', task.design_mode_cfg)}\n"
        f"- 完成标记：{'是' if state.get('finished') else '否'}\n"
        f"- 进展日志：{P.progress_file}\n"
        f"- 更新时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
        f"## 仓库基线\n\n| 仓库 | 路径 | 基线 |\n|------|------|------|\n{wt}\n\n"
        f"## 阶段\n\n| 阶段 | 状态 | 说明 |\n|------|------|------|\n" + "\n".join(rows) + "\n\n"
        f"## 恢复\n\n```\npython3 {TOOL_DIR / 'flow.py'} -c {task.cfg_path} --resume\n```\n")
    (P.docs_dir / f"flow-state-{task.req_id}-{task.date}.md").write_text(text, encoding="utf-8")


def mark(state, run_dir, stage, status, detail=""):
    state["stages"][stage] = {"status": status, "detail": detail,
                              "ts": dt.datetime.now().isoformat(timespec="seconds")}
    hb(run_dir, stage, status)
    write_state(run_dir, state)


def write_state(run_dir, state):
    (run_dir / "stages.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def launch_watchdog(task, run_dir):
    if not task.watchdog:
        return None
    cmd = [sys.executable, str(TOOL_DIR / "watchdog.py"),
           "--run-dir", str(run_dir), "--flow-pid", str(os.getpid()),
           # 心跳停滞阈值比单次调用超时放宽 30 分钟，避免长调用被误报
           "--stage-timeout-min", str(task.stage_timeout_min + 30),
           "--total-timeout-min", str(task.total_timeout_hours * 60),
           "--poll-sec", "30", "--req-id", task.req_id]
    return subprocess.Popen(cmd, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- 检查与状态查询

def cmd_check(task, gcfg, ping):
    problems = []
    if not task.core_goals:
        problems.append("goals.core_goals 未填写（各阶段提示词的核心目标来源）")
    if not task.verify_commands:
        problems.append("goals.verify_commands 未填写（TDD/评审/修复的验证命令来源）")
    for r in task.repos:
        if not (r.src / ".git").exists():
            problems.append(f"{r.src} 不是 git 仓库")
    used = sorted(set(task.roles.values()) - {""})
    for key in used:
        try:
            name, role = agents.build_role(gcfg, key, [])
            log(f"角色就绪 {name}: {role.describe()}")
        except SystemExit as e:
            problems.append(str(e))
    mode = resolve_design_mode(task, gcfg)
    if mode == "arena":
        n = len((gcfg.get("panel") or {}).get("arena") or [])
        log(f"设计模式：arena（{n} 个候选并行 + 裁判选一）")
    else:
        log(f"设计模式：single（单模型 + {task.self_review_rounds} 轮自评审修复）")
    s4r = task.s4.get("reviewer") or gcfg.get("leader") or {}
    s4f = task.s4.get("fixer") or (gcfg.get("worker") or {}).get("code") or {}
    for tag, rc in (("S4 评审", s4r), ("S4 修复", s4f)):
        if rc:
            try:
                Role_probe(rc, tag)
            except SystemExit as e:
                problems.append(f"{tag}角色不可用：{e}")
    if problems:
        raise SystemExit("检查未通过：\n- " + "\n- ".join(problems))
    log(f"检查通过：req_id={task.req_id}，仓库 {len(task.repos)} 个，"
        f"worktree={'启用 → ' + str(task.worktree_parent / task.req_id) if task.use_worktree else '直连原仓'}，"
        f"S4 轮数上限 {task.s4.get('max_rounds', 20)}")
    if ping:
        ping_dir = Path.home() / ".cache" / "devflow" / "ping"
        ping_dir.mkdir(parents=True, exist_ok=True)
        for key in used:
            name, role = agents.build_role(gcfg, key, [])
            log(f"连通测试 {name}")
            try:
                result = role.run("这是一次连通性测试。请只回复四个字：连通正常",
                                  str(task.workdir), ping_dir / f"{name.replace(':', '_')}.log", 300, 1)
                log(f"  返回：{result.strip()[:40]}")
            except LoopStop as e:
                raise SystemExit(f"{name} 连通失败：{e}")


def Role_probe(rcfg, tag):
    """S4 内联角色只做构造校验（不发起调用）。"""
    from loop import Role
    Role(tag, rcfg, [], str(SKILLS_DIR))


def cmd_status(req_id, watch):
    root = CACHE_ROOT / req_id
    if not root.exists():
        raise SystemExit(f"没有 {req_id} 的运行记录（{root}）")
    runs = [d for d in root.iterdir() if d.is_dir()]
    run = max(runs, key=lambda d: d.stat().st_mtime)
    meta = {}
    if (run / "meta.json").exists():
        meta = json.loads((run / "meta.json").read_text(encoding="utf-8"))
    seen = None
    while True:
        state = json.loads((run / "stages.json").read_text(encoding="utf-8")) if (run / "stages.json").exists() else {}
        heart = json.loads((run / "heartbeat.json").read_text(encoding="utf-8")) if (run / "heartbeat.json").exists() else {}
        rows = [f"| {st} | {info.get('status')} | {info.get('detail', '')} |"
                for st, info in state.get("stages", {}).items()]
        age = int(time.time() - heart["ts"]) if heart.get("ts") else -1
        text = (
            f"运行目录：{run}\n"
            f"任务配置：{meta.get('task_config', '?')}　docs：{meta.get('docs_dir', '?')}\n"
            f"完成标记：{state.get('finished', False)}\n"
            f"心跳：{heart.get('stage')} / {heart.get('step')} （{age}s 前）\n\n"
            "| 阶段 | 状态 | 说明 |\n|------|------|------|\n" + "\n".join(rows)
        )
        for f in run.glob("watchdog-report-*.md"):
            text += f"\n看门狗报告：{f}"
        if text != seen:
            print("\n" + text, flush=True)
            seen = text
        if not watch:
            return
        time.sleep(30)


# ---------------------------------------------------------------- 主流程

def run_flow(task, args):
    gcfg = agents.load_config()
    run_dir = CACHE_ROOT / task.req_id / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}"
    run_dir.mkdir(parents=True)
    state = {"stages": {}, "worktrees": {}, "finished": False}
    if args.resume:  # 沿用最近一次运行的阶段完成状态，产物在 docs 目录本就持久
        prior = sorted(d for d in (CACHE_ROOT / task.req_id).iterdir() if d.is_dir())
        for old in reversed(prior):
            if old != run_dir and (old / "stages.json").exists():
                old_state = json.loads((old / "stages.json").read_text(encoding="utf-8"))
                state["stages"] = {k: v for k, v in old_state.get("stages", {}).items()
                                   if v.get("status") == "done"}
                log(f"--resume：沿用 {old.name} 中已完成的阶段：{'、'.join(state['stages']) or '无'}")
                break

    started = time.time()
    P = resolve_paths(task, run_dir, state)
    (run_dir / "meta.json").write_text(json.dumps(
        {"req_id": task.req_id, "task_config": str(task.cfg_path), "docs_dir": str(P.docs_dir),
         "workdir": str(P.workdir), "repos": {k: str(v) for k, v in P.repos.items()},
         "start": dt.datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    mark(state, run_dir, "W0", "done", "; ".join(state["worktrees"].values()))
    write_flow_state(task, P, state, run_dir)
    wd = None if args.no_watchdog else launch_watchdog(task, run_dir)

    todo = STAGES
    if args.stage:
        wanted = [s.strip().upper() for s in args.stage.split(",")]
        bad = [s for s in wanted if s not in STAGES + ["S5"]]
        if bad:
            raise SystemExit(f"未知阶段 {bad}，可用：{' '.join(STAGES)} S5")
        todo = [s for s in STAGES if s in wanted]
    elif args.resume:
        todo = [s for s in STAGES if state["stages"].get(s, {}).get("status") != "done"]
    if args.stage and "S5" in [s.strip().upper() for s in args.stage.split(",")] and "S4" not in todo:
        todo.append("S5")
    log_progress(P, "flow", "start",
                 f"本次运行启动（--resume={bool(args.resume)}），计划阶段：{'、'.join(todo) or '无'}",
                 f"运行目录 {run_dir}；任务配置 {task.cfg_path}")

    sharpened = (run_dir / "sharpened.md").read_text(encoding="utf-8") if (run_dir / "sharpened.md").exists() else ""
    if args.resume:  # 旧 run 目录里的锐化稿也带过来
        for old in sorted((CACHE_ROOT / task.req_id).iterdir(), reverse=True):
            if (old / "sharpened.md").exists():
                sharpened = (old / "sharpened.md").read_text(encoding="utf-8")
                break

    outcome = "通过：全流程完成"
    try:
        for st in todo:
            if time.time() - started > task.total_timeout_hours * 3600:
                raise FlowStop(f"总时长超过 {task.total_timeout_hours}h，安全暂停")
            mark(state, run_dir, st, "running")
            if st == "S0":
                sharpened = stage_s0(gcfg, task, P, run_dir)
            elif st == "S1":
                stage_s1(gcfg, task, P, run_dir, sharpened)
            elif st == "S2":
                stage_s2(gcfg, task, P, run_dir, state)
            elif st == "S3":
                stage_s3(gcfg, task, P, run_dir)
            elif st == "S4":
                stage_s4(gcfg, task, P, run_dir, state)
            elif st == "S5":
                stage_s5(gcfg, task, P, run_dir)
            mark(state, run_dir, st, "done")
            write_flow_state(task, P, state, run_dir)
    except (FlowStop, LoopStop) as e:
        cur = next((s for s in reversed(STAGES) if state["stages"].get(s, {}).get("status") == "running"), "?")
        mark(state, run_dir, cur, "failed", str(e)[:200])
        log_progress(P, cur, "安全暂停", f"{e}", f"恢复命令：flow.py -c {task.cfg_path} --resume；运行目录 {run_dir}")
        log_decision(P.docs_dir, cur, "阶段失败安全暂停", "FlowStop/LoopStop", run_dir, str(e)[:200])
        outcome = f"安全暂停：{e}"
        state["finished"] = True
        write_state(run_dir, state)
        write_flow_state(task, P, state, run_dir)
        log(outcome)
        log(f"恢复命令：python3 {TOOL_DIR / 'flow.py'} -c {task.cfg_path} --resume")
        sys.exit(1)
    except KeyboardInterrupt:
        mark(state, run_dir, next((s for s in STAGES if state["stages"].get(s, {}).get("status") == "running"), "?"),
             "failed", "用户中断")
        state["finished"] = True
        write_state(run_dir, state)
        sys.exit(130)

    state["finished"] = True
    write_state(run_dir, state)
    write_flow_state(task, P, state, run_dir)
    log_decision(P.docs_dir, "flow", "全流程完成", "一键 prompt→需求→设计→TDD→评审→技术文档",
                 str(run_dir), "ok")
    log(outcome)
    log(f"文档目录：{P.docs_dir}　运行记录：{run_dir}")
    if wd is not None:
        log("看门狗将在进程退出后写出报告（run 目录 watchdog-report-*.md）")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        ap = argparse.ArgumentParser(description="查看最近一次运行的实时状态")
        ap.add_argument("req_id")
        ap.add_argument("--watch", action="store_true", help="持续输出（供监控使用，Ctrl-C 退出）")
        a = ap.parse_args(sys.argv[2:])
        cmd_status(a.req_id, a.watch)
        return
    ap = argparse.ArgumentParser(description="dev-flow 一键全流程确定性执行器（任务配置见 tasks/example.toml）")
    ap.add_argument("-c", "--config", required=True, help="任务配置 toml 路径")
    ap.add_argument("--check", action="store_true", help="只校验配置/仓库/角色，不调模型")
    ap.add_argument("--ping", action="store_true", help="--check 基础上做角色连通测试")
    ap.add_argument("--resume", action="store_true", help="从首个未完成阶段继续")
    ap.add_argument("--stage", help="只跑指定阶段，逗号分隔：W0 S0 S1 S2 S3 S4 S5")
    ap.add_argument("--no-watchdog", action="store_true", help="不启动看门狗进程")
    args = ap.parse_args()
    task = Task(args.config)
    if args.check or args.ping:
        cmd_check(task, agents.load_config(), args.ping)
        return
    run_flow(task, args)


if __name__ == "__main__":
    main()
