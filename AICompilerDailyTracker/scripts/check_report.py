#!/usr/bin/env python3
"""报告机械校验脚本（AICompilerDailyTracker 技能专用）。

对最终报告 md 做结构与格式的机械校验（发布前核查的机器部分）：
  1. 三章节结构完整（进展摘要/参考文献/友情提醒）；
  2. 摘要条目 [n] 编号连续、条数 <= 10、每条含书名号《》标题、"针对""提出"句式、尾部引用 [n]；
  3. 参考文献条目编号连续、每条含 http(s) 链接；
  4. 摘要引用编号与参考文献编号一一对应；
  5. 友情提醒每条含等级方括号、至少一个日期、网址；日期须落在 [today, today+3个月] 窗口内；
  6. 报告中所有 URL 格式合法；
  7. 报告标题中的区间日期与 --start/--end 一致。

语义级核查（时间真实性、主题相关性、覆盖完整性、提醒日期与官网一致性）由
SKILL.md 的核查清单人工执行，本脚本只管机械可判定项。

用法：
  python3 check_report.py 报告.md --start 2026-11-15 --end 2026-11-15 [--today 2026-11-16]
退出码：0 全部通过；1 存在 FAIL 项。
"""

import argparse
import datetime
import re
import sys

URL_RE = r"https?://[^\s，。）)\]\}】；;、]+"


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("report", help="报告 md 文件路径")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--today", default=datetime.date.today().isoformat())
    return ap.parse_args()


def main():
    args = parse_args()
    text = open(args.report, encoding="utf-8").read()
    lines = text.splitlines()
    fails, warns = [], []

    def fail(msg):
        fails.append(msg)

    # 1. 章节结构
    for head in ("跟踪进展摘要", "参考文献", "友情提醒"):
        if not any(head in ln and ln.lstrip().startswith("#") for ln in lines):
            fail(f"缺少章节：{head}")

    # 切分章节（保留原始行，用于行尾硬换行检查）
    def section(title):
        idx = [i for i, ln in enumerate(lines) if title in ln and ln.lstrip().startswith("#")]
        if not idx:
            return []
        s = idx[0] + 1
        e = len(lines)
        for j in range(s, len(lines)):
            if lines[j].lstrip().startswith("#"):
                e = j
                break
        return lines[s:e]

    abstract_raw = section("跟踪进展摘要")
    refs_raw = section("参考文献")
    remind_raw = section("友情提醒")

    # 0. 分行规范：编号条目行行尾必须两个空格（Markdown 硬换行），否则渲染时条目粘连
    for sec_name, sec in (("摘要", abstract_raw), ("参考文献", refs_raw), ("提醒", remind_raw)):
        for ln in sec:
            if re.match(r"\s*\[\d+\]", ln) and not ln.rstrip("\n").endswith("  "):
                fails.append(f"{sec_name}条目 {ln.strip()[:30]}… 行尾缺两个空格（Markdown 硬换行），渲染会粘连")

    abstract = [ln for ln in abstract_raw if ln.strip()]
    refs = [ln for ln in refs_raw if ln.strip()]
    remind = [ln for ln in remind_raw if ln.strip()]

    # 2. 摘要条目
    items = [ln.strip() for ln in abstract if re.match(r"\s*\[\d+\]", ln)]
    if not items:
        fail("进展摘要没有任何 [n] 条目")
    if len(items) > 10:
        fail(f"进展摘要 {len(items)} 条，超过 10 条上限")
    for i, it in enumerate(items, 1):
        m = re.match(r"\[(\d+)\]", it)
        if int(m.group(1)) != i:
            fail(f"摘要第 {i} 条编号为 [{m.group(1)}]，应为 [{i}]（须从 1 连续编号）")
        if "《" not in it or "》" not in it:
            fail(f"摘要条目 [{i}] 缺少《论文标题》")
        if "针对" not in it or "提出" not in it:
            fail(f"摘要条目 [{i}] 缺少“针对…问题，提出…方法”句式")
        tail = re.findall(r"\[(\d+)\]", it)
        if not tail or int(tail[-1]) != i:
            fail(f"摘要条目 [{i}] 尾部引用标号应为 [{i}]")

    # 3. 参考文献条目
    ref_items = [ln.strip() for ln in refs if re.match(r"\s*\[\d+\]", ln)]
    if not ref_items:
        fail("参考文献没有任何 [n] 条目")
    for i, it in enumerate(ref_items, 1):
        m = re.match(r"\[(\d+)\]", it)
        if int(m.group(1)) != i:
            fail(f"参考文献第 {i} 条编号为 [{m.group(1)}]，应为 [{i}]")
        if not re.search(URL_RE, it):
            fail(f"参考文献 [{i}] 缺少网址")

    # 4. 编号对应
    a_ids = {int(re.match(r"\[(\d+)\]", it).group(1)) for it in items}
    r_ids = {int(re.match(r"\[(\d+)\]", it).group(1)) for it in ref_items}
    if a_ids != r_ids:
        fail(f"摘要引用编号 {sorted(a_ids)} 与参考文献编号 {sorted(r_ids)} 不一致")
    if items and a_ids != set(range(1, len(items) + 1)):
        fail("条目编号不连续")

    # 5. 友情提醒
    r_items = [ln.strip() for ln in remind if re.match(r"\s*\[\d+\]", ln)]
    if not r_items:
        fail("友情提醒没有任何 [n] 条目")
    try:
        t0 = datetime.date.fromisoformat(args.today)
        t1 = t0 + datetime.timedelta(days=92)
    except ValueError:
        t0 = t1 = None
    for it in r_items:
        if not re.search(r"\[[A-Za-z+?／/]+\]", it):
            warns.append(f"友情提醒条目 {it[:30]}… 未见 [等级] 方括号，请人工确认等级标注")
        if not re.search(URL_RE, it):
            fail(f"友情提醒条目 {it[:40]}… 缺少官网网址")
        dates = re.findall(r"(\d{4})-(\d{2})-(\d{2})", it)
        if not dates:
            fail(f"友情提醒条目 {it[:40]}… 缺少 YYYY-MM-DD 格式日期")
        elif t0:
            in_win = []
            for y, mo, d in dates:
                try:
                    dd = datetime.date(int(y), int(mo), int(d))
                    in_win.append(t0 <= dd <= t1)
                except ValueError:
                    pass
            if in_win and not any(in_win):
                fail(f"友情提醒条目 {it[:40]}… 的日期均不在 [{t0}, {t1}] 3 个月窗口内")

    # 6. URL 合法性（全文）
    for u in re.findall(URL_RE, text):
        if u.rstrip('.,；;') != u or "…" in u or "。" in u:
            fail(f"疑似残缺 URL：{u[:60]}")

    # 7. 标题区间一致性
    head1 = next((ln for ln in lines if ln.startswith("# ")), "")
    for d in (args.start, args.end):
        if d not in head1:
            fail(f"报告标题未包含区间日期 {d}：{head1!r}")

    # 结果
    for w in warns:
        print(f"WARN {w}")
    if fails:
        for f_ in fails:
            print(f"FAIL {f_}")
        print(f"\n共 {len(fails)} 项 FAIL，{len(warns)} 项 WARN —— 校验未通过，修复后重跑")
        sys.exit(1)
    print(f"PASS 机械校验通过：摘要 {len(items)} 条，参考文献 {len(ref_items)} 条，"
          f"投稿提醒 {len(r_items)} 条，WARN {len(warns)} 项")


if __name__ == "__main__":
    main()
