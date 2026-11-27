"""旁白稿 md 的机械校验：页面结构、字数预算、格式、口语、数字溯源。

用法: .venv/bin/python scripts/check_narration.py 旁白稿.md [--extract <extract目录>]

错误(E)必须修复后才能生成 docx；警告(W)需逐条人工确认（确认无误可放行）。
数字溯源：旁白里出现的数字（十位以上或带小数点）逐个到论文全文 text.md 里找，
找不到的列出——要么是换算/推导值（应在稿中写明换算关系），要么是写错或编造。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

H1_RE = re.compile(r"^# ")
H2_RE = re.compile(r"^##\s+第(\d+)页\s*(.*)$")
BOLD_TOTAL_RE = re.compile(r"^\*\*旁白总字数[：:]")
# 中文语境里连续 ≥4 个英文单词，疑似整句英文
EN_MIX_RE = re.compile(r"[A-Za-z]+[^A-Za-z\s]{0,2}\s+(?:[A-Za-z]+\s+){3,}[A-Za-z]+")
SPOKEN_EN = re.compile(
    r"[一-鿿]\s*(the|and|of|with|for|to|in|is|are|we|our|can|will|that|this|from|by|not|but)\b"
    r"[\s,,.:：、，。；;]", re.IGNORECASE)
NUM_RE = re.compile(r"(?<![A-Za-z0-9_.])(\d+(?:\.\d+)?)(?![A-Za-z0-9_])")
# 颜色表述硬规则：「红是什么」不合格，必须「红色/红色区域」
COLOR_BARE_RE = re.compile(r"[红橙黄绿蓝紫青灰粉白黑](?=[是代表即即为])")
URL_RE = re.compile(r"https?://|arxiv\.org|doi\.org|\b10\.\d{4,}|www\.")
HEADING_LIST_RE = re.compile(r"启发[两三四1234]条|启发如下|启发有以下")
LINK_WORD_RE = re.compile(r"下一步|作为.{0,8}输入|交给|送到|传递给|得到.{0,10}再")
CHART_MARK_RE = re.compile(r"横轴|纵轴|横坐标|纵坐标")
METRIC_DIR_RE = re.compile(r"越大越好|越小越好|越高越好|越低越好|越长越好|越短越好|越多越好|越少越好")
SELF_INSIGHT_RE = re.compile(r"通过这次阅读|给我的启发|我的启发|读.{0,6}启发")
FILLER = ("卡住", "对症", "说白了", "所以呢", "说白", "咱们", "呃，", "嗯，", "那个那个")
SECTIONS = ["一.论文概述", "二.研究背景", "三.研究问题", "四.传统研究现状",
            "五.本文研究方法", "六.示例说明", "七.实验结果", "八.总结"]
NARRATION_MAX = 7000   # 30 分钟 × 200 字/分钟（讲清优先，不做压缩）
NARRATION_MIN = 5000
NARRATION_TARGET_MIN = 5500
COVER_MAX = 160        # 封面/参考文献/结束页等短页上限
PAGE_MIN, PAGE_MAX = 200, 560
MIN_PAGES, MAX_PAGES = 18, 26

errors: list[str] = []
warns: list[str] = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warns.append(msg)


def plain(line: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", line)


def page_paras(lines, start):
    """第 start 行是 ## 第N页 标题；返回该页的正文段（到下一个 ##/# 之前）。
    保留行首全角空格：str.strip 会剥掉 U+3000，前缀校验就失效了。"""
    out = []
    for l in lines[start + 1:]:
        if l.startswith("#"):
            break
        s = l.rstrip()
        core = s.lstrip("　 \t")
        if core and not BOLD_TOTAL_RE.match(core):
            out.append(s)
    return out


def check(md_path: Path, extract: Path | None):
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    src_text = extract.joinpath("text.md").read_text(encoding="utf-8") if extract else ""

    # ---------- 1. 文档与页面结构 ----------
    h1 = [l for l in lines if H1_RE.match(l)]
    if len(h1) != 1:
        err(f"文档标题(# )应有且仅有 1 个，实际 {len(h1)} 个")
    pages = [(int(m.group(1)), m.group(2)) for l in lines if (m := H2_RE.match(l))]
    if not pages:
        err("没有任何「## 第N页 …」页面标题")
        return
    nums = [n for n, _ in pages]
    if nums != list(range(1, len(pages) + 1)):
        err(f"页码应从第1页连续编号，当前: {nums}")
    if not MIN_PAGES <= len(pages) <= MAX_PAGES:
        err(f"总页数 {len(pages)}，应在 {MIN_PAGES}–{MAX_PAGES} 页")
    titles = [t for _, t in pages]
    joined = " → ".join(titles)
    for sec in SECTIONS:
        if not any(t.startswith(sec) for t in titles):
            err(f"八节结构缺少「{sec}」对应的页：{joined[:80]}")

    # ---------- 2. 每页旁白 ----------
    total = 0
    idx_of = {}
    for i, l in enumerate(lines):
        if (m := H2_RE.match(l)):
            idx_of[len(idx_of) + 1] = (i, int(m.group(1)), m.group(2))
    for pno, (i, num, title) in sorted(idx_of.items()):
        paras = page_paras(lines, i)
        if not paras:
            err(f"第{num}页「{title}」没有旁白正文")
            continue
        n_chars = sum(len(plain(p.lstrip("　 "))) for p in paras)
        total += n_chars
        for p in paras:
            if not p.startswith("　　"):
                err(f"第{num}页：旁白段落未以两个全角空格开头: {plain(p)[:24]}")
                break
        if num == 1 or num == len(pages) or title.startswith("参考文献"):
            if n_chars > COVER_MAX:
                warn(f"第{num}页（封面/参考文献/结束页）旁白 {n_chars} 字，建议 ≤{COVER_MAX} 字，当前偏长")
        elif n_chars < PAGE_MIN:
            warn(f"第{num}页「{title}」旁白仅 {n_chars} 字，这一页讲不满 1 分钟，考虑并页或充实内容")
        elif n_chars > PAGE_MAX:
            warn(f"第{num}页「{title}」旁白 {n_chars} 字，超过 {PAGE_MAX} 字，单页讲超过 2.5 分钟")
        if title.startswith("八."):
            body = "".join(plain(p) for p in paras)
            for word in ("不足", "启发"):
                if word not in body:
                    err(f"第{num}页总结旁白没有讲到「{word}」——不足与启发是硬性要求")
        if title.startswith("六."):
            body = "".join(plain(p) for p in paras)
            if "示例" not in body and "例子" not in body:
                err(f"第{num}页示例说明旁白没有出现「示例/例子」——旁白要带着听众逐步推演")
            if "图" not in body:
                err(f"第{num}页示例说明旁白没有讲到图——流程图要逐步指着讲")
            if not LINK_WORD_RE.search(body):
                err(f"第{num}页示例旁白没有步骤衔接——每步要说清输出如何成为下一步的输入"
                    f"（「作为下一步的输入/交给/送到」等）")
        if title.startswith("四."):
            body = "".join(plain(p) for p in paras)
            if not re.search(r"分为|分成|两类|三类|四类|几类|一类", body):
                err(f"第{num}页现状旁白没有总起分类（「已有研究分为N类…」）")
            if "原理" not in body or "不足" not in body:
                err(f"第{num}页现状旁白没有按「每类原理→由原理推出的不足」讲")
        if title.startswith("八."):
            body = "".join(plain(p) for p in paras)
            if HEADING_LIST_RE.search(body):
                err(f"第{num}页总结旁白用了「启发N条/启发如下」列举式开头——"
                    f"必须第一人称感受式（「通过这次阅读，我的启发是…」）")
            if "启发" in body and not SELF_INSIGHT_RE.search(body):
                err(f"第{num}页总结旁白讲启发但没用第一人称感受式"
                    f"（「通过这次阅读，给我的启发是…」）")
        if title.startswith("参考文献") or "参考文献" in title:
            body = "".join(plain(p) for p in paras)
            if len([s for s in re.split(r"[。！？]", body) if s.strip()]) > 2:
                err(f"第{num}页参考文献旁白超过 2 句——只说「下面是论文的相关参考文献」即可，不逐条念")

    # ---------- 3. 总字数 ----------
    print(f"[check] 旁白总字数 {total} 字（约 {total/200.0:.1f} 分钟）")
    if total > NARRATION_MAX:
        err(f"旁白总字数 {total} 字超过 {NARRATION_MAX} 字（30 分钟上限），必须删减")
    elif total < NARRATION_MIN:
        err(f"旁白总字数 {total} 字少于 {NARRATION_MIN} 字，内容太薄")
    elif total < NARRATION_TARGET_MIN:
        warn(f"旁白总字数 {total} 字略低于 {NARRATION_TARGET_MIN} 字目标")

    # ---------- 4. 口语与中英混排 ----------
    for i, l in enumerate(lines):
        raw = l.strip()
        if not raw or raw.startswith(("#", "**", ">")):
            continue
        s = plain(raw)
        hit = [w for w in FILLER if w in s]
        if hit:
            warn(f"第{i+1}行: 旁白含口语「{'、'.join(hit)}」: {s[:36]}")
        if EN_MIX_RE.search(s):
            warn(f"第{i+1}行: 疑似整句英文混入中文旁白: {s[:40]}")
        if SPOKEN_EN.search(s):
            warn(f"第{i+1}行: 疑似中英混排，除专有名词外应使用中文: {s[:36]}")
        if re.search(r"[一-鿿]\s*[,;]\s*[一-鿿]", s):
            warn(f"第{i+1}行: 中文语句中使用了半角标点: {s[:36]}")

    # ---------- 4.5 表达规范：色词、网址、电报式省略 ----------
    for i, l in enumerate(lines):
        raw = l.strip()
        if not raw or raw.startswith(("#", "**", ">")):
            continue
        s = plain(raw)
        m = COLOR_BARE_RE.search(s)
        if m:
            errors.append(f"第{i+1}行: 「{m.group(0)}」缺「色」——颜色表述必须说「红色区域/蓝色柱」"
                          f"等完整说法: {s[:36]}")
        if URL_RE.search(s):
            errors.append(f"第{i+1}行: 旁白念了网址/DOI——网址只放页面上，解说词不念: {s[:36]}")
    for pno, (i, num, title) in sorted(idx_of.items()):
        paras = page_paras(lines, i)
        body = "".join(plain(p) for p in paras)
        if len(body) < 100:
            continue
        sents = [s for s in re.split(r"[。！？]", body) if len(s.strip()) >= 2]
        if sents:
            short = sum(1 for s in sents if len(s.strip()) < 9)
            if short / len(sents) > 0.2:
                warns.append(f"第{num}页「{title}」超短句占比 {short}/{len(sents)}"
                             f"——旁白像电报，要把句子捋顺（「橙是X」→「橙色区域是X」）")
        if CHART_MARK_RE.search(body) and not METRIC_DIR_RE.search(body) \
                and not re.search(r"对比|比较|对照", body):
            warns.append(f"第{num}页「{title}」讲了轴但没说指标方向/比对对象"
                         f"（越大越好还是越小越好？比哪些方法？）")

    # ---------- 5. 数字溯源 ----------
    if src_text:
        strip_k = lambda t: re.sub(r"(?<=\d),\s?(?=\d{3}\b)", "", t)
        src = strip_k(src_text)
        src_nums = set(m.group(1) for m in NUM_RE.finditer(src))
        src_nums |= set(re.findall(r"\d+(?:\.\d+)?", src))
        missing = {}
        for i, l in enumerate(lines):
            raw = l.strip()
            if not raw or raw.startswith(("#", "**", ">")):
                continue
            s = plain(raw)
            for m in NUM_RE.finditer(strip_k(s)):
                n = m.group(1)
                if "." not in n and float(n) <= 10:
                    continue
                alt = n.rstrip("0").rstrip(".") if "." in n else n
                if n not in src_nums and alt not in src_nums:
                    missing.setdefault(n, []).append(i + 1)
        for n, where in sorted(missing.items(), key=lambda kv: -len(kv[1]))[:20]:
            warn(f"数字 {n} 在论文全文中未找到（第{where[:3]}行等）。若是换算值请写明依据，否则修正")


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
