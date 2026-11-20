#!/usr/bin/env python3
"""devflow 外部模型调度器。

读取 dev-flow/config.toml 中的角色配置，复用 review-fix-loop/loop.py 的引擎层
（claude / kimi / codex 三种 CLI 的登录、settings 切换厂商、会话清理），
为 arena（多候选）、interrogate（对抗评审）、swarm（并行 worker）等技能提供
统一的外部模型调用入口。每次调用都是独立进程中的全新对话。

用法：
    python3 agents.py list                              # 列出配置的角色与面板候选
    python3 agents.py run <角色> -p "提示词"            # 运行一个角色
    python3 agents.py run <角色> --prompt-file p.md     # 提示词来自文件
    python3 agents.py check <角色> [<角色> ...]         # 连通测试（每个角色发一句话）

角色写法：
    leader / doc / code          单角色
    worker:doc / worker:code     同上（等价写法）
    arena:<name 或 序号>         panel.arena 列表中的候选，如 arena:glm、arena:0
    interrogate:<name 或 序号>   panel.interrogate 列表中的攻击者

常用选项：
    --workdir DIR        模型进程工作目录（通常是任务 worktree），默认当前目录
    --add-dir DIR        额外可访问目录，可多次
    --timeout-min N      超时分钟数，默认 60
    --max-turns N        限制轮数（claude 引擎）
    --log FILE           日志文件，默认 ~/.cache/devflow/logs/<角色>-<时间>.log
"""
import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import tomllib

TOOL_DIR = Path(__file__).resolve().parent
SKILLS_DIR = TOOL_DIR.parent
sys.path.insert(0, str(SKILLS_DIR / "review-fix-loop"))
from loop import LoopStop, Role, setup_proxy  # noqa: E402  复用评审-修复循环的引擎实现

LOG_DIR = Path.home() / ".cache" / "devflow" / "logs"
PANELS = ("arena", "interrogate")


def load_config():
    path = TOOL_DIR / "config.toml"
    if not path.exists():
        raise SystemExit("缺少 dev-flow/config.toml：先复制 config.example.toml 为 config.toml 并填写")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def resolve_role(cfg, key):
    """把角色写法解析为 (显示名, 角色配置dict, 代理需求)；找不到时报错退出。"""
    if key in ("leader",):
        return "leader", cfg.get("leader"), False
    alias = {"doc": "worker.doc", "code": "worker.code",
             "worker:doc": "worker.doc", "worker:code": "worker.code"}
    if key in alias:
        group, _, name = alias[key].partition(".")
        return alias[key], (cfg.get(group) or {}).get(name), False
    panel, _, sel = key.partition(":")
    if panel in PANELS and sel:
        entries = (cfg.get("panel") or {}).get(panel) or []
        for i, ent in enumerate(entries):
            if sel == str(ent.get("name", "")) or sel == str(i):
                return f"{panel}:{ent.get('name', i)}", ent, bool(ent.get("proxy"))
        raise SystemExit(f"panel.{panel} 中没有 name 或序号为 {sel} 的条目")
    raise SystemExit(f"无法识别的角色：{key}（可用：leader doc code arena:<x> interrogate:<x>）")


def build_role(cfg, key, add_dirs):
    name, rcfg, need_proxy = resolve_role(cfg, key)
    if not rcfg:
        raise SystemExit(f"config.toml 中没有配置角色 {name}")
    proxy_env = None
    if need_proxy:
        # 代理命令复用 review-fix-loop/config.toml 的 [proxy] 配置
        loop_cfg_path = SKILLS_DIR / "review-fix-loop" / "config.toml"
        if not loop_cfg_path.exists():
            raise SystemExit(f"[{name}] 需要 proxy 但 review-fix-loop/config.toml 不存在")
        loop_cfg = tomllib.loads(loop_cfg_path.read_text(encoding="utf-8"))
        proxy_env = setup_proxy(loop_cfg.get("proxy", {}))
    role = Role(name, rcfg, add_dirs, str(SKILLS_DIR), proxy_env)
    return name, role


def cmd_list(_args):
    cfg = load_config()
    rows = []
    for single in ("leader",):
        if cfg.get(single):
            rows.append((single, cfg[single]))
    for k, v in (cfg.get("worker") or {}).items():
        rows.append((f"worker:{k}", v))
    for panel in PANELS:
        for i, ent in enumerate((cfg.get("panel") or {}).get(panel) or []):
            rows.append((f"{panel}:{ent.get('name', i)}", ent))
    if not rows:
        print("config.toml 中没有配置任何角色")
        return
    print(f"{'角色':<22} 引擎     模型/厂商配置")
    for name, c in rows:
        where = c.get("settings") or c.get("base_url") or ""
        print(f"{name:<22} {c.get('engine', 'claude'):<8} {c.get('model') or where}")


def run_role(cfg, key, prompt, workdir, add_dirs, timeout_min, max_turns, log_file):
    name, role = build_role(cfg, key, add_dirs)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = Path(log_file) if log_file else LOG_DIR / f"{name.replace(':', '_')}-{dt.datetime.now():%Y%m%d-%H%M%S}.log"
    try:
        result = role.run(prompt, str(Path(workdir).resolve()), log, timeout_min * 60, max_turns)
    except LoopStop as e:
        print(f"[{name}] 调用失败：{e}", file=sys.stderr)
        sys.exit(1)
    return result


def main():
    ap = argparse.ArgumentParser(description="devflow 外部模型调度器（角色配置见 config.example.toml）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出配置的角色与面板候选")

    r = sub.add_parser("run", help="运行一个角色，输出最终回复")
    r.add_argument("role", help="leader / doc / code / arena:<x> / interrogate:<x>")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("-p", "--prompt", help="提示词正文")
    g.add_argument("--prompt-file", help="提示词文件路径")
    r.add_argument("--workdir", default=".", help="模型进程工作目录（默认当前目录）")
    r.add_argument("--add-dir", action="append", default=[], help="额外可访问目录，可多次")
    r.add_argument("--timeout-min", type=int, default=60, help="超时分钟数（默认 60）")
    r.add_argument("--max-turns", type=int, default=None, help="限制轮数（claude 引擎）")
    r.add_argument("--log", default=None, help="日志文件路径（默认自动命名）")

    c = sub.add_parser("check", help="连通测试：每个角色发一句问候")
    c.add_argument("roles", nargs="+", help="要测试的角色")

    args = ap.parse_args()
    if args.cmd == "list":
        cmd_list(args)
        return
    if args.cmd == "check":
        cfg = load_config()
        for key in args.roles:
            print(f"--- 测试 {key} ---")
            result = run_role(cfg, key, "这是一次连通性测试。请只回复四个字：连通正常",
                              ".", [], 10, None, None)
            print(f"回复：{result.strip()[:100]}")
        return
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    result = run_role(load_config(), args.role, prompt, args.workdir, args.add_dir,
                      args.timeout_min, args.max_turns, args.log)
    print(result)


if __name__ == "__main__":
    main()
