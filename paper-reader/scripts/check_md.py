"""论文总结 md 的机械校验：结构、图表编号、格式、中英混排、术语首现解释、数字溯源。

用法: .venv/bin/python scripts/check_md.py <summary.md> [--extract <extract目录>]

错误(E)必须修复后才能生成 docx；警告(W)需逐条人工确认（确认无误可忽略）。
结构：九章齐全有序（含「六.示例说明」）；概述恰 1 段、段末注明论文外网链接
（确无公开链接时写明「未找到公开链接」）并有「如图」引导封面图；全文图1 为
论文首页封面且位于「一.论文概述」段后、「二.研究背景」前；示例说明章有正文段落。
术语首现解释：英文术语/缩写第一次出现时，同句应有中文解释（括号释义或"是/指/即/称"），
保证只读本文即可理解全文；查不到解释的列出，需补定义或确认确属无需解释的常识词。
数字溯源：正文里出现的数字（十位以上或带小数点）逐个到论文全文 text.md 里找，
找不到的列出——要么是换算/推导值（应在文中写明换算关系），要么是写错或编造；
「六.示例说明」内自构示例的数值豁免溯源（须在文中标注「示例取值」）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

IMG_RE = re.compile(r"^!\[[^\]]*\]\(([^)]+)\)\s*$")
FIG_CAP_RE = re.compile(r"^图\s*(\d+)")
TAB_CAP_RE = re.compile(r"^表\s*(\d+)")
H1_RE = re.compile(r"^# ")
H2_RE = re.compile(r"^##\s+([^ ].*)")
LIST_RE = re.compile(r"^(\d+[.、)]|[-*•])\s")
# 题注可用 <p align="center">…</p> 包裹实现 md 预览居中；校验与构建前统一剥离
ALIGN_TAG_RE = re.compile(r"^<p\s+align=[\"']?center[\"']?>\s*(.*?)\s*</p>$", re.IGNORECASE)
# 术语首现解释检查
TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*")
TERM_SKIP = {  # 通用单位、硬件常识缩写与上下文自明的单词（全部小写，比较时小写化）
    "gb", "kb", "mb", "tb", "gib", "mib", "tib", "ms", "us", "ns", "ghz", "mhz",
    "gpu", "cpu", "npu", "dram", "sram", "hbm", "ai", "ok", "id", "case", "ch",
    "key", "value", "query", "score", "and", "the", "of", "for",
}
# 中文语境里连续 ≥4 个英文单词，疑似整句英文/中英混排
EN_MIX_RE = re.compile(r"[A-Za-z]+[^A-Za-z\s]{0,2}\s+(?:[A-Za-z]+\s+){3,}[A-Za-z]+")
# 常见必须中文表述的英文口语词（正文禁止，图题/引用/专有名词除外）
SPOKEN_EN = re.compile(
    r"[一-鿿]\s*(the|and|of|with|for|to|in|is|are|we|our|can|will|that|this|from|by|not|but)\b"
    r"[\s,,.:：、，。；;]", re.IGNORECASE)
NUM_RE = re.compile(r"(?<![A-Za-z0-9_.])(\d+(?:\.\d+)?)(?![A-Za-z0-9_])")
URL_RE = re.compile(r"https?://\S+")   # 链接里的数字/单词不参与术语与数字校验
CN = "一二三四五六七八"
SECTIONS = ["论文概述", "研究背景", "研究问题", "传统研究现状", "本文研究方法", "示例说明", "实验结果", "总结"]

errors: list[str] = []
warns: list[str] = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warns.append(msg)


def plain(line: str) -> str:
    """去掉 md 标记与对齐标签后的纯文本。"""
    m = ALIGN_TAG_RE.match(line.strip())
    if m:
        line = m.group(1)
    return re.sub(r"\*\*(.+?)\*\*", r"\1", line)


def check(md_path: Path, extract: Path | None):
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    src_text = extract.joinpath("text.md").read_text(encoding="utf-8") if extract else ""

    # ---------- 1. 章节结构 ----------
    h1 = [l for l in lines if H1_RE.match(l) and not l.startswith("## ")]
    if len(h1) != 1:
        err(f"文档标题(# )应有且仅有 1 个，实际 {len(h1)} 个")
    h2s = [H2_RE.match(l).group(1).strip() for l in lines if H2_RE.match(l)]
    for idx, want in enumerate(SECTIONS, 1):
        cn = CN[idx - 1]
        if not any(re.match(rf"{cn}[.、、]?\s*{want}", h) for h in h2s):
            err(f"缺少第{CN[idx-1]}节「{cn}.{want}」或标题书写不规范")
    seq = [h for h in h2s if re.match(r"^[一二三四五六七八][.、]?", h)]
    order = [CN.index(h[0]) for h in seq]
    if order != sorted(order):
        err(f"章节顺序错误：{seq}")
    if not any(h.startswith("附录") for h in h2s):
        err("缺少「附录」")

    # ---------- 2. 概述/总结的段落数 ----------
    def norm_sec(title):
        return re.sub(rf"^[{CN}][.、]?\s*", "", title.strip())

    def para_count(sec_name):
        n, inside = 0, False
        for l in lines:
            if H2_RE.match(l):
                inside = norm_sec(H2_RE.match(l).group(1)).startswith(sec_name)
            elif inside and l.strip() and not l.strip().startswith(
                    ("!", "图", "表", "|", "#", ">", "<", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "0.")):
                if not LIST_RE.match(l.strip()):
                    n += 1
        return n

    if (n := para_count("论文概述")) != 1:
        err(f"「一.论文概述」应为 1 个自然段，实际 {n} 段")
    # 概述段末须注明外网链接，并用「如图」引导随后的首页封面图
    ov_text = "\n".join(plain(l) for l in section_lines(lines, "论文概述"))
    if not re.search(r"https?://", ov_text) and "未找到公开链接" not in ov_text:
        err("「一.论文概述」段末须注明论文的外网可访问链接（arXiv/DOI/官网；确无公开链接时如实写「未找到公开链接」）")
    if "如图" not in ov_text:
        err("「一.论文概述」须用「如图 1 所示」式表述引导随后的首页封面图")
    bg = para_count("研究背景")
    if not 1 <= bg <= 3:
        warn(f"「二.研究背景」建议 1~2 段，实际 {bg} 段")
    if not 1 <= para_count("研究问题") <= 4:
        warn("「三.研究问题」建议高度概括（1~2 句 + 可选示例），请确认篇幅")
    concl_text = "\n".join(plain(l) for l in section_lines(lines, "总结"))
    if not re.search(r"(不足|局限|改进|优化|未来|展望)", concl_text):
        err("「七.总结」必须包含对本文不足与后续改进空间的提炼（关键要求）")
    if para_count("总结") > 2:
        err("「七.总结」应为 1 段总结（不足与改进可并入同段或紧随其后 1 段），当前段数过多")

    # ---------- 3. 图片 ----------
    fig_nums, img_lines = [], []
    for i, l in enumerate(lines):
        if (m := IMG_RE.match(l.strip())):
            img_lines.append((i, m.group(1)))
    for i, rel in img_lines:
        if not (md_path.parent / rel).exists():
            err(f"第{i+1}行: 图片文件不存在 {rel}")
        # 图题必须紧跟其后（允许一个空行）
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        cap = plain(lines[j]) if j < len(lines) else ""
        if j >= len(lines) or not FIG_CAP_RE.match(cap):
            err(f"第{i+1}行: 图片后缺少「图N 题目」图题行")
        else:
            fig_nums.append(int(FIG_CAP_RE.match(cap).group(1)))
            if not re.search(r"(摘自原文|改绘自原文|自绘)", cap):
                warn(f"第{j+1}行: 图题建议注明来源（摘自原文 Figure N / 自绘）: {cap[:30]}")
        try:
            from PIL import Image
            import numpy as np
            with Image.open(md_path.parent / rel) as im:
                w, h = im.size
            if w < 400:
                warn(f"第{i+1}行: 图片宽度仅 {w}px，清晰度可能不足: {rel}")
            if not 0.2 <= w / h <= 6:
                warn(f"第{i+1}行: 图片长宽比 {w/h:.1f} 过于极端，版面效果差: {rel}")
            a = np.asarray(im.convert("L").resize((min(w, 400), min(h, 400))))
            for side, ink in (("上", a[0] < 180), ("下", a[-1] < 180),
                              ("左", a[:, 0] < 180), ("右", a[:, -1] < 180)):
                if ink.mean() > 0.08:
                    warn(f"第{i+1}行: 图片{side}边缘有大量内容，疑似裁剪不完整: {rel}")
        except Exception:
            pass
    if fig_nums != sorted(set(fig_nums)):
        warn(f"图编号未按出现顺序递增: {fig_nums}")
    if fig_nums and fig_nums != list(range(1, len(fig_nums) + 1)):
        err(f"图编号应从图1连续编号，当前: {fig_nums}")
    # 全文第 1 张图应为论文首页封面图，且位于概述段之后、研究背景之前
    if img_lines:
        i0 = img_lines[0][0]
        j = i0 + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        cap0 = plain(lines[j]) if j < len(lines) else ""
        m0 = FIG_CAP_RE.match(cap0)
        if not m0 or m0.group(1) != "1" or "封面" not in cap0:
            err("全文第 1 张图应为论文首页封面图，图题形如「图1 论文首页封面（题目与作者信息，摘自原文第 1 页）」")
        bg_idx = next((k for k, l in enumerate(lines)
                       if H2_RE.match(l) and norm_sec(H2_RE.match(l).group(1)).startswith("研究背景")), len(lines))
        if i0 > bg_idx:
            err("首页封面图应放在「一.论文概述」段末之后、「二.研究背景」之前")

    # ---------- 4. 表格 ----------
    tab_nums = []
    for i, l in enumerate(lines):
        if l.strip().startswith("|") and i + 1 < len(lines) and lines[i + 1].strip().startswith("|") \
                and set(lines[i + 1].strip()) <= set("|-: "):
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            tcap = plain(lines[j]) if j >= 0 else ""
            if j < 0 or not TAB_CAP_RE.match(tcap):
                err(f"第{i+1}行: 表格上方缺少「表N 题目」表题行")
            else:
                tab_nums.append(int(TAB_CAP_RE.match(tcap).group(1)))
            ncol = l.strip().count("|") - 1
            k = i + 2
            while k < len(lines) and lines[k].strip().startswith("|"):
                nc = lines[k].strip().count("|") - 1
                if nc != ncol:
                    err(f"第{k+1}行: 表格列数不一致（{nc} != {ncol}）")
                k += 1
    if tab_nums and tab_nums != list(range(1, len(tab_nums) + 1)):
        err(f"表编号应从表1连续编号，当前: {tab_nums}")

    # ---------- 5. 每节开头必须是文字段落 ----------
    for i, l in enumerate(lines):
        if H2_RE.match(l):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                t = plain(lines[j])
                if t.startswith(("!", "|", "图", "表")) or IMG_RE.match(t):
                    err(f"第{j+1}行: 「{H2_RE.match(l).group(1).strip()}」节开头必须是总结性文字段落，不能直接是图表")

    # ---------- 6. 段落格式 ----------
    for i, l in enumerate(lines):
        s = l.rstrip("\n")
        if not s.strip() or H1_RE.match(s) or H2_RE.match(s) or s.strip().startswith(("!", "|", ">", "#", "图", "表", "<")):
            continue
        if LIST_RE.match(s.strip()):
            continue
        if s.startswith("    ") or s.startswith("\t"):
            err(f"第{i+1}行: 使用了 4 空格/Tab 缩进（markdown 会渲染为代码块），段落请用全角空格「　　」开头")
        elif not s.startswith("　　"):
            warn(f"第{i+1}行: 正文段落未以两个全角空格开头: {plain(s)[:30]}")
        if s != s.rstrip():
            err(f"第{i+1}行: 行尾有多余空白")

    # ---------- 7. 中英混排 ----------
    for i, l in enumerate(lines):
        s = URL_RE.sub("", plain(l.strip()))
        if not s or s.startswith(("!", "|", ">", "#", "图", "表")) or LIST_RE.match(s):
            continue
        if EN_MIX_RE.search(s):
            warn(f"第{i+1}行: 疑似整句英文混入中文段落（专有名词引用除外）: {s[:40]}")
        m = SPOKEN_EN.search(s)
        if m:
            warn(f"第{i+1}行: 疑似中英混排（{m.group(1)}），除专有名词外应使用中文: {s[:40]}")
        # 中文语境中的半角标点
        if re.search(r"[一-鿿]\s*[,;]\s*[一-鿿]", s) or re.search(r"[一-鿿]\.(\s|$)", s):
            warn(f"第{i+1}行: 中文语句中使用了半角标点: {s[:40]}")

    # ---------- 8. 附录条数 ----------
    app, inside = 0, False
    for l in lines:
        if H2_RE.match(l):
            inside = re.sub(rf"^[{CN}][.、]?\s*", "", H2_RE.match(l).group(1).strip()).startswith("附录")
        elif inside and l.strip() and LIST_RE.match(l.strip()):
            app += 1
    if app == 0:
        err("附录没有任何条目")
    elif app > 10:
        err(f"附录条目应不超过 10 条，当前 {app} 条")

    # ---------- 8b. 示例说明 ----------
    ex_lines = section_lines(lines, "示例说明")
    ex_paras = sum(1 for l in ex_lines if l.strip()
                   and not l.strip().startswith(("!", "图", "表", "|", "#", ">", "<"))
                   and not LIST_RE.match(l.strip()))
    if ex_paras == 0:
        err("「六.示例说明」缺少正文段落")
    if not any(IMG_RE.match(l.strip()) for l in ex_lines):
        warn("「六.示例说明」应配一张贯穿示例的流程图（draw_flow.py 自绘，或摘自原文示例图）")
    ex_text = "\n".join(plain(l) for l in ex_lines)
    if ex_text and not re.search(r"(摘自原文|论文自带|自构|自设|示例取值|示例数据)", ex_text):
        warn("「六.示例说明」应注明示例来源（论文自带须注明摘自原文；自构建议标注「示例取值」）")

    # ---------- 9. 术语首现解释（自洽性） ----------
    # 英文术语/缩写第一次出现时，同句应有中文解释。认可的"已解释"线索：
    #   1) 括注别名：术语位于括号开头（含"全名（TERM…）""（Commit Zone）"）
    #   2) 术语后 45 字内跟带中文的括号释义：TERM …（…中文…）
    #   3) 术语后 25 字内有"是/指/即/称为/对应"式定义（前邻 1~2 个汉字）
    #   4) 术语前紧跟"提出/设计/构建/实现"等引入动词（本文提出 TERM…）
    #   5) 术语紧跟文献引用编号（TERM[46]，后文随之描述）
    #   6) 术语后 40 字内出现"等+类名"（…、TERM 等+中文类名）
    #   7) 术语身处含中文的括号内（（TERM…，中文说明…））
    #   8) 术语后紧跟纯英文括注（全名（缩写）：Order-1-K（O1K））
    # 只在正文段落查（标题、题注、附录条目豁免）；首现无解释的报警告。
    first_seen = {}
    for i, l in enumerate(lines):
        s = URL_RE.sub("", plain(l.strip()))
        if not s or s.startswith(("!", "|", ">", "#", "图", "表")) or LIST_RE.match(s):
            continue
        for m in TERM_RE.finditer(s):
            term = m.group(0)
            if len(term) < 2 or term.lower() in TERM_SKIP or not re.search(r"[A-Z]", term):
                continue
            if term in first_seen:
                continue
            tail = s[m.end():m.end() + 60]
            head = s[max(0, m.start() - 16):m.start()]
            te = re.escape(term)
            # 术语起点落在某个含中文的括号区间内
            in_zh = False
            for pm in re.finditer(r"[（(]([^（）()]*)[)）]", s):
                if pm.start() < m.start() < pm.end() and re.search(r"[一-鿿]", pm.group(1)):
                    in_zh = True
                    break
            explained = (
                re.search(r"[（(]\s*" + te + r"\b", s)                              # 1 括注别名
                or re.search(r"[（(](?:[A-Za-z][A-Za-z0-9-]*\s+){0,3}" + te, s)      # 1b 多词括注
                or re.search(r"^[^（）()]{0,24}[（(][^（）()]*[一-鿿]", tail[:45])     # 2 括号释义
                or re.search(r"[一-鿿]{1,2}\s*(?:是|指|即|称为|指的是|对应)", tail[:25])  # 3 定义动词
                or re.search(r"(?:提出|设计|构建|实现|开发|命名|引入)\s*$", head)     # 4 引入动词
                or re.match(r"\s*\[\d+\]", tail)                                     # 5 带引用编号
                or re.search(r"等[一-鿿]", tail[:40])                                # 6 等类名
                or in_zh                                                              # 7 中文括号内
                or re.match(r"\s*[（(][A-Za-z0-9\s/+.~×-]*[)）]", tail)               # 8 纯英文括注
            )
            first_seen[term] = (i + 1, bool(explained), s[:36])
    unexplained = [(t, v) for t, v in first_seen.items() if not v[1]]
    for t, (ln, _, ctx) in sorted(unexplained, key=lambda kv: kv[1][0]):
        warn(f"术语 {t} 首次出现（第{ln}行）未见中文解释。要求：只读本文即可理解，"
             f"请在首现处补一句定义或说明用途: {ctx}")

    # ---------- 10. 数字溯源 ----------
    if src_text:
        strip_k = lambda t: re.sub(r"(?<=\d),\s?(?=\d{3}\b)", "", t)  # 去千分位
        src = strip_k(src_text)
        src_nums = set(m.group(1) for m in NUM_RE.finditer(src))
        src_nums |= set(re.findall(r"\d+(?:\.\d+)?", src))
        # 示例说明章的自构数值不参与溯源（要求文中标注「示例取值」）
        ex_line_idx, inside_ex = set(), False
        for i, l in enumerate(lines):
            if H2_RE.match(l):
                inside_ex = norm_sec(H2_RE.match(l).group(1)).startswith("示例说明")
            elif inside_ex:
                ex_line_idx.add(i)
        missing = {}
        for i, l in enumerate(lines):
            if i in ex_line_idx:
                continue
            s = URL_RE.sub("", plain(l.strip()))
            if not s or s.startswith(("!", "|", ">", "#", "图", "表")):
                continue
            for m in NUM_RE.finditer(strip_k(s)):
                n = m.group(1)
                if "." not in n and float(n) <= 10:   # 小整数、编号不查
                    continue
                alt = n.rstrip("0").rstrip(".") if "." in n else n
                if n not in src_nums and alt not in src_nums:
                    missing.setdefault(n, []).append(i + 1)
        for n, where in sorted(missing.items(), key=lambda kv: -len(kv[1]))[:25]:
            warn(f"数字 {n} 在论文全文中未找到（第{where[:3]}行等）。若是换算值请在文中写明依据，否则修正")


def section_lines(lines, sec_prefix):
    out, inside = [], False
    for l in lines:
        if H2_RE.match(l):
            inside = re.sub(rf"^[一二三四五六七八][.、]?\s*", "", H2_RE.match(l).group(1).strip()).startswith(sec_prefix)
        elif inside:
            out.append(l)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("md")
    ap.add_argument("--extract", default=None, help="extract 目录（用于数字溯源）")
    args = ap.parse_args()
    md = Path(args.md)
    if not md.exists():
        print(f"[check] 错误: 找不到 {md}", file=sys.stderr)
        sys.exit(2)
    extract = Path(args.extract) if args.extract else None
    if extract and not extract.joinpath("text.md").exists():
        print(f"[check] 警告: {extract}/text.md 不存在，跳过数字溯源", file=sys.stderr)
        extract = None
    check(md, extract)
    for w in warns:
        print(f"W  {w}")
    for e in errors:
        print(f"E  {e}")
    print(f"\n[check] 错误 {len(errors)} 项，警告 {len(warns)} 项")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
