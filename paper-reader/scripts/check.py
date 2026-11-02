"""content.json 校验：结构、图片引用、版面溢出、旁白时长、数字溯源。

用法: pr.sh check <content.json>

数字溯源：把幻灯片、旁白、Word 总结里出现的数字（如 0.9%、26×、12.8M）逐个到论文全文
extract/text.md 里查找，找不到的列出来——它们要么是换算/推导出来的（需要在旁白里说明），
要么是写错或编造的，必须修正。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from common import load_content, load_figures, plain, resolve_figure
from layout import LAYOUTS, SlideError, layout_slide, overflow_report

CHARS_PER_MIN = 320           # edge-tts 中文默认语速实测约 5.3 字/秒（含页间停顿）
SLIDE_CHARS = (40, 420)       # 单页旁白字数建议范围（封面/结束页除外）
NUM_RE = re.compile(r"(?<![A-Za-z0-9_.])(\d+(?:[.,]\d+)*)(?![A-Za-z0-9_])")
# 不需要溯源的数字：页码式小整数、年份等
IGNORE_SMALL = 10


def chars_per_min(rate: str | None) -> float:
    """按 tts.rate（如 "+5%"、"-10%"）修正每分钟字数。"""
    try:
        pct = int(str(rate or "0").strip().rstrip("%"))
    except ValueError:
        pct = 0
    return CHARS_PER_MIN * (1 + pct / 100.0)


def _numbers(text: str):
    for m in NUM_RE.finditer(plain(text)):
        n = m.group(1).replace(",", "")
        try:
            v = float(n)
        except ValueError:
            continue
        if "." not in n and v <= IGNORE_SMALL:
            continue
        yield n


def _iter_texts(content):
    for i, s in enumerate(content.get("slides", []), 1):
        where = f"第{i}页"
        for k in ("title", "subtitle", "takeaway", "caption", "narration", "note"):
            if s.get(k):
                yield where + ("旁白" if k == "narration" else ""), s[k]
        for b in s.get("bullets", []) or []:
            if isinstance(b, str):
                yield where, b
            else:
                yield where, b.get("text", "")
                for sub in b.get("sub", []) or []:
                    yield where, sub
        for m in s.get("metrics", []) or []:
            yield where, " ".join(str(m.get(k, "")) for k in ("value", "label", "desc"))
        for r in [s.get("header") or []] + (s.get("rows") or []):
            yield where, " ".join(map(str, r))
    summ = content.get("summary", {})
    yield "Word", summ.get("tldr", "")
    for sec in summ.get("sections", []):
        for b in sec.get("blocks", []):
            if b.get("type") == "h2":
                continue  # 小节编号不做溯源
            for k in ("text", "caption"):
                if b.get(k):
                    yield "Word·" + sec.get("heading", ""), b[k]
            for it in b.get("items", []) or []:
                yield "Word·" + sec.get("heading", ""), it
            for r in [b.get("header") or []] + (b.get("rows") or []):
                yield "Word·" + sec.get("heading", ""), " ".join(map(str, r))


def number_trace(content, source: str):
    src = re.sub(r"(?<=\d),\s?(?=\d{3}\b)", "", source)  # 去掉千分位，含 "13, 700" 这类排版空格
    src_nums = set(m.group(1) for m in NUM_RE.finditer(src))
    src_nums |= set(re.findall(r"\d+(?:\.\d+)?", src))
    missing = {}
    allow = set(map(str, content.get("check", {}).get("allow_numbers", [])))
    for where, text in _iter_texts(content):
        for n in _numbers(text):
            if n in src_nums or n in allow:
                continue
            # 允许 1.50 与 1.5 这类等价写法
            alt = n.rstrip("0").rstrip(".") if "." in n else n
            if alt in src_nums:
                continue
            missing.setdefault(n, set()).add(where)
    return missing


def run(content_path: Path) -> bool:
    content_path = Path(content_path).resolve()
    content = load_content(content_path)
    errors, warns = [], []
    meta = content.get("meta", {})
    for k in ("title",):
        if not meta.get(k):
            errors.append(f"meta.{k} 缺失")
    slides = content.get("slides") or []
    if not slides:
        errors.append("slides 为空")
    if not content.get("summary"):
        errors.append("summary 缺失（Word 总结的内容）")
    figs = load_figures(content_path, content)
    if not figs:
        warns.append("没有找到 figures.json，图片只能用相对路径引用")

    total_chars = 0
    for i, s in enumerate(slides, 1):
        t = s.get("type", "bullets")
        if t not in LAYOUTS:
            errors.append(f"第 {i} 页：未知页型 {t!r}")
            continue
        try:
            els = layout_slide(s, meta, figs, content_path, i, len(slides))
            for m in overflow_report(els):
                errors.append(f"第 {i} 页：{m}")
        except SlideError as ex:
            errors.append(f"第 {i} 页：{ex}")
        n = len(plain(s.get("narration", "")))
        total_chars += n
        if t not in ("cover", "end", "section"):
            if n == 0:
                errors.append(f"第 {i} 页（{s.get('title', '')}）没有旁白")
            elif not SLIDE_CHARS[0] <= n <= SLIDE_CHARS[1]:
                warns.append(f"第 {i} 页旁白 {n} 字，建议 {SLIDE_CHARS[0]}–{SLIDE_CHARS[1]} 字")
        for ref in [s.get("figure")] + [f.get("ref") for f in s.get("figures", []) or []]:
            if ref and isinstance(ref, str) and ref not in figs and not (content_path.parent / ref).exists():
                errors.append(f"第 {i} 页：图片引用 {ref!r} 不存在")
    for sec in content.get("summary", {}).get("sections", []):
        for b in sec.get("blocks", []):
            if b.get("type") == "figure" and resolve_figure(b.get("ref"), figs, content_path) is None:
                errors.append(f"Word「{sec.get('heading')}」：图片引用 {b.get('ref')!r} 不存在")

    target = content.get("target_minutes")
    est = total_chars / chars_per_min(content.get("tts", {}).get("rate"))
    if target and not (0.8 * target <= est <= 1.2 * target):
        warns.append(f"旁白共 {total_chars} 字，预计 {est:.1f} 分钟，偏离目标 {target} 分钟超过 20%")

    src_path = content_path.parent / "extract" / "text.md"
    if src_path.exists():
        miss = number_trace(content, src_path.read_text(encoding="utf-8"))
        for n, where in sorted(miss.items()):
            warns.append(f"数字 {n} 在论文原文中找不到（出现在：{'、'.join(sorted(where))}），"
                         "请确认是否为换算值，否则修正；确认无误可加入 check.allow_numbers")

    print(f"[check] {len(slides)} 页，旁白 {total_chars} 字，预计 {est:.1f} 分钟")
    for w in warns:
        print(f"[check] 警告：{w}")
    for e in errors:
        print(f"[check] 错误：{e}")
    if not errors:
        print("[check] 通过" + ("（有警告，请逐条确认）" if warns else ""))
    sys.stdout.flush()
    return not errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("content")
    a = ap.parse_args()
    raise SystemExit(0 if run(Path(a.content)) else 1)


if __name__ == "__main__":
    main()
