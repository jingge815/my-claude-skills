#!/usr/bin/env python3
"""dev-flow 看门狗：独立进程监控 flow.py 的心跳与存活性，避免死锁或运行时间过长无人知晓。

只观察与记录，唯一主动干预是总时长超限时终止流程进程树：
- stage_stalled（WARN）：heartbeat.json 超过 --stage-timeout-min 未更新。单次模型调用的
  超时由 flow.py 传给引擎层执行，这里只兜底报告，默认不杀（--kill-on-stall 可改）。
- died：flow 进程死亡且 stages.json 未标记 finished。
- total_timeout：超过 --total-timeout-min，记事故并 kill 进程树。
- heartbeat_missing：有阶段在跑但心跳文件不存在（流程工具缺陷）。

事故追加到 run 目录 watchdog-incidents.tsv 与 docs 目录 progress 进展日志；结束（含被
看门狗终止后）生成 watchdog-report-<需求标识>.md：事故时间线 + 面向流程系统的改进建议，
并在 decisions.tsv 补一行。用法见 flow.py 的 launch_watchdog，一般不手工运行。
"""
import argparse
import datetime as dt
import json
import os
import signal
import sys
import time
from pathlib import Path

SUGGEST = {
    "stage_stalled": ("阶段心跳停滞：调大任务配置 [monitor].stage_timeout_minutes，或用 --stage 把慢阶段"
                      "拆出来单独跑；检查 run 目录对应阶段 *.log 尾部，常见原因是模型重试风暴或外部 CLI 卡死。"),
    "died": ("flow 进程异常退出：查看 run 目录各阶段 *.log 尾部定位死因；常见为外部 CLI 崩溃、网络中断、"
             "被系统 OOM。建议给角色 env 配置重试，并把该阶段用 --resume 补跑。"),
    "total_timeout": ("总时长超限被终止：用 --stage 分阶段补跑剩余部分，或调大 [monitor].total_timeout_hours、"
                      "下调 [s4_loop].max_rounds / no_progress_max，并检查 verify_commands 里是否有小时级命令。"),
    "heartbeat_missing": ("心跳缺失但流程在跑：该阶段没有走 hb/log_progress 标准封装，属流程工具缺陷，"
                          "请给该阶段补心跳调用后更新 dev-flow。"),
}
STATIC_SUGGEST = [
    "定期归档 ~/.cache/devflow/runs 与 ~/.cache/review-fix-loop/runs，避免磁盘被运行记录占满。",
    "复盘 decisions.tsv 中各阶段耗时，把经常超时的阶段超时在任务配置中单独调大。",
]


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def children(pid):
    """扫 /proc 找直接子进程（无需 psutil）。"""
    out = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            stat = (p / "stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        if ppid == pid:
            out.append(int(p.name))
    return out


def kill_tree(pid):
    for c in children(pid):
        kill_tree(c)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def wlog(run_dir, msg):
    with (run_dir / "watchdog.log").open("a", encoding="utf-8") as f:
        f.write(f"[{dt.datetime.now():%H:%M:%S}] {msg}\n")


def incident(run_dir, kind, stage, detail):
    path = run_dir / "watchdog-incidents.tsv"
    if not path.exists():
        path.write_text("ts\tkind\tstage\tdetail\n", encoding="utf-8")
    row = [dt.datetime.now().isoformat(timespec="seconds"), kind, str(stage), detail]
    with path.open("a", encoding="utf-8") as f:
        f.write("\t".join(c.replace("\t", " ").replace("\n", " ") for c in row) + "\n")


def progress_append(meta, req_id, stage, step, progress, evidence=""):
    """把事故同步进 docs 目录的 progress 进展日志（与 flow.py 同一格式）。"""
    docs = meta.get("docs_dir", "")
    if not docs:
        return
    docs = Path(docs)
    files = sorted(docs.glob(f"progress-{req_id}-*.md"))
    path = files[-1] if files else docs / f"progress-{req_id}-{dt.date.today():%Y%m%d}.md"
    if not path.exists():
        docs.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# 工作进展：{docs}\n\n按时间追加；决策与理由见 decisions.tsv。\n\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"## {dt.datetime.now():%Y-%m-%d %H:%M:%S}｜{stage}｜{step}\n- 进展：{progress}\n")
        if evidence:
            f.write(f"- 证据：{evidence}\n")
        f.write("\n")


def write_report(run_dir, req_id, meta):
    incidents = []
    path = run_dir / "watchdog-incidents.tsv"
    if path.exists():
        incidents = [l.split("\t") for l in path.read_text(encoding="utf-8").splitlines()[1:] if l]
    state = read_json(run_dir / "stages.json", {})
    kinds = [row[1] for row in incidents]
    lines = [f"# 看门狗报告：{req_id}", "",
             f"- 运行目录：{run_dir}",
             f"- 结束时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}",
             f"- 流程完成标记：{'是' if state.get('finished') else '否'}", "",
             "## 阶段状态", "", "| 阶段 | 状态 | 说明 |", "|------|------|------|"]
    for st, info in (state.get("stages") or {}).items():
        lines.append(f"| {st} | {info.get('status')} | {info.get('detail', '')} |")
    lines += ["", "## 事故时间线", ""]
    if incidents:
        lines += ["| 时间 | 类型 | 阶段 | 详情 |", "|------|------|------|------|"]
        lines += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} |" for r in incidents]
    else:
        lines.append("无事故。")
    lines += ["", "## 流程系统改进建议", ""]
    seen = []
    for k in dict.fromkeys(kinds):
        if k in SUGGEST:
            lines.append(f"- [{k}] {SUGGEST[k]}")
            seen.append(k)
    if not seen:
        lines += [f"- {s}" for s in STATIC_SUGGEST]
    report = run_dir / f"watchdog-report-{req_id}.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    docs = meta.get("docs_dir", "")
    if docs:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "review-fix-loop"))
        try:
            from loop import log_decision
            log_decision(docs, "watchdog", "看门狗结束",
                         "心跳/存活性监控" + (f"，事故：{','.join(dict.fromkeys(kinds))}" if kinds else "，无事故"),
                         report.name, "ok" if state.get("finished") else "流程未完成")
        except ImportError:
            pass


def main():
    ap = argparse.ArgumentParser(description="dev-flow 看门狗（由 flow.py 拉起，也可手工运行）")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--flow-pid", type=int, required=True)
    ap.add_argument("--stage-timeout-min", type=float, default=270.0)
    ap.add_argument("--total-timeout-min", type=float, default=1440.0)
    ap.add_argument("--poll-sec", type=float, default=30.0)
    ap.add_argument("--req-id", default="task")
    ap.add_argument("--kill-on-stall", action="store_true", help="心跳停滞也终止流程（默认只报告）")
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = read_json(run_dir / "meta.json", {})
    start = time.time()
    emitted = set()

    while True:
        hbdata = read_json(run_dir / "heartbeat.json")
        state = read_json(run_dir / "stages.json", {})
        finished = bool(state.get("finished"))
        alive = pid_alive(args.flow_pid)

        if hbdata:
            age = time.time() - hbdata["ts"]
            key = f"stall:{hbdata.get('stage')}:{hbdata.get('step')}"
            if age > args.stage_timeout_min * 60 and key not in emitted:
                emitted.add(key)
                detail = f"心跳停滞 {int(age / 60)} 分钟（{hbdata.get('stage')} / {hbdata.get('step')}）"
                incident(run_dir, "stage_stalled", hbdata.get("stage"), detail)
                progress_append(meta, args.req_id, "watchdog", "心跳停滞", detail,
                                f"{run_dir}/heartbeat.json；{run_dir}/watchdog-incidents.tsv")
                wlog(run_dir, detail)
                if args.kill_on_stall:
                    kill_tree(args.flow_pid)
                    time.sleep(args.poll_sec)
                    break
        elif not finished and alive and "hb_missing" not in emitted:
            running = [s for s, i in (state.get("stages") or {}).items() if i.get("status") == "running"]
            if running:
                emitted.add("hb_missing")
                incident(run_dir, "heartbeat_missing", ",".join(running), "有阶段在跑但心跳文件缺失")
                wlog(run_dir, "心跳文件缺失")

        if not alive:
            if not finished:
                incident(run_dir, "died", "-", f"flow 进程 {args.flow_pid} 死亡且未标记完成")
                progress_append(meta, args.req_id, "watchdog", "进程异常退出",
                                f"flow 进程 {args.flow_pid} 死亡且未标记完成", f"{run_dir}/watchdog-incidents.tsv")
            break
        if time.time() - start > args.total_timeout_min * 60:
            detail = f"总时长超过 {int(args.total_timeout_min)} 分钟，终止流程进程树"
            incident(run_dir, "total_timeout", "-", detail)
            progress_append(meta, args.req_id, "watchdog", "总时长超限", detail,
                            f"{run_dir}/watchdog-incidents.tsv")
            wlog(run_dir, detail)
            kill_tree(args.flow_pid)
            time.sleep(args.poll_sec)  # 留时间让 flow/循环落盘，之后直接出报告
            break
        time.sleep(args.poll_sec)

    write_report(run_dir, args.req_id, meta)
    wlog(run_dir, "报告已生成")


if __name__ == "__main__":
    main()
