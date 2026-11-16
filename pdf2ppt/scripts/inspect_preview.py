"""预览图程序化版面检测（门禁3）：逐页量标题/正文/图/总结区的覆盖率。

用法: .venv/bin/python scripts/inspect_preview.py <preview目录> --content content.json

背景说明：预览是渲染结果，肉眼逐页看最可靠；本脚本把「看过」变成可执行的机械检查——
每页的标题区、正文区、图区、总结区都必须有内容；图区不允许整块空白；单图页的图
在图槽里的实际占高不得过小（版面空）。阈值按模板几何与实测预览标定，偏保守：
错误(E)必须修复（改 content.json / figs 后重新生成再重跑）；警告(W)逐条确认。

依赖 preview_slides.py 先产出 preview/slide-NN.png。LibreOffice 渲染与 PowerPoint
有字体差异，只看覆盖与布局，不看字形。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image

SLIDE_W, SLIDE_H = 33.87, 19.05  # cm（16:9）
# 与区域背景中位亮度差超过该值算墨。标定依据：模板标题是深蓝横幅（亮度≈71）上的
# 更深色文字（≈39），对比度只有 Δ32——阈值取 30 才能检到标题；预览为 PNG 无压缩噪声，
# 30 不会误报（正文/图区对比度普遍 >60）。
INK_DELTA = 30
E_TITLE, E_BODY, E_FIG = 0.004, 0.010, 0.008   # 各区墨覆盖下限（E）
W_FIG = 0.015           # 图区覆盖偏软（W）
FIG_BBOX_MIN = 0.30     # 单图页：图实际占高低于图槽 30% 判版面空（W）

# 模板几何（cm），与 references/template-spec.md 第二节一致
REGIONS = {
    "text-left":  {"title": (1.13, 0.32, 26.9, 1.75), "body": (1.13, 4.6, 14.2, 16.8),
                   "fig": (14.58, 4.89, 32.74, 14.16), "summary": (2.78, 17.0, 33.3, 18.2)},
    "figure-left": {"title": (1.13, 0.32, 26.9, 1.75), "body": (20.2, 4.6, 32.5, 16.8),
                    "fig": (1.13, 4.89, 19.29, 14.16), "summary": (2.78, 17.0, 33.3, 18.2)},
    "status":     {"title": (1.13, 0.32, 26.9, 1.75), "body": (1.45, 4.1, 12.1, 15.7),
                   "fig_top": (13.13, 2.31, 32.5, 10.50), "fig_bottom": (13.13, 10.80, 32.5, 18.60)},
    "refs":       {"title": (1.13, 0.32, 26.9, 1.75), "body": (1.13, 4.13, 32.7, 16.5)},
    "cover":      {"title": (1.7, 3.1, 32.2, 9.8), "meta": (21.2, 14.7, 32.6, 18.1)},
    "closing":    {"takeaway": (4.7, 4.2, 29.1, 6.5), "thanks": (1.17, 11.1, 32.7, 13.1)},
}

errors: list[str] = []
warns: list[str] = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warns.append(msg)


def region_stats(im, box):
    """区域墨覆盖 = 与区域背景中位亮度差 > INK_DELTA 的像素占比；返回 (覆盖率, 墨行集合)。"""
    W, H = im.size
    sx, sy = W / SLIDE_W, H / SLIDE_H
    r = im.crop((int(box[0] * sx), int(box[1] * sy), int(box[2] * sx), int(box[3] * sy))).convert("L")
    w, h = r.size
    if w <= 0 or h <= 0:
        return 0.0, []
    px = list(r.get_flattened_data() if hasattr(r, "get_flattened_data") else r.getdata())
    px_sorted = sorted(px)
    bg = px_sorted[len(px) // 2]
    mask = [v for v in px if abs(v - bg) > INK_DELTA]
    ink_rows = set()
    for y in range(h):
        row = px[y * w:(y + 1) * w]
        if any(abs(v - bg) > INK_DELTA for v in row[::max(1, w // 64)]):
            ink_rows.add(y)
    return len(mask) / len(px), sorted(ink_rows)


def bbox_span(rows, h):
    """墨行集合的纵向跨度占比（图实际占了图槽多高）。"""
    if not rows:
        return 0.0
    return (rows[-1] - rows[0] + 1) / max(h, 1)


def page_figures(page):
    fig = page.get("figure")
    if fig is None or fig == "" or fig == "图/表":
        return 0
    return len(fig) if isinstance(fig, list) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("preview_dir")
    ap.add_argument("--content", required=True, help="content.json 路径")
    args = ap.parse_args()
    pdir = Path(args.preview_dir)
    content = json.loads(Path(args.content).read_text(encoding="utf-8"))
    pages = content.get("pages") or []

    pngs = sorted(pdir.glob("slide-*.png"), key=lambda p: int(re.search(r"(\d+)", p.stem).group(1)))
    if not pngs:
        print(f"[inspect] 错误: {pdir} 下没有 slide-NN.png，先跑 preview_slides.py", file=sys.stderr)
        sys.exit(2)
    if len(pngs) != len(pages):
        err(f"预览 {len(pngs)} 页与 content.json {len(pages)} 页不一致，先重新生成预览")

    for idx, png in enumerate(pngs):
        i = idx + 1
        if i > len(pages):
            break
        kind = pages[idx].get("kind") or ""
        regs = REGIONS.get(kind)
        if regs is None:
            warn(f"第{i}页 kind「{kind}」没有对应的检测区域，跳过")
            continue
        im = Image.open(png)
        line = f"p{i:<2} {kind:<11}"
        for name, box in regs.items():
            cov, rows = region_stats(im, box)
            h_px = int((box[3] - box[1]) / SLIDE_H * im.size[1])
            line += f" {name}={cov*100:4.1f}%"
            limit = {"title": E_TITLE, "body": E_BODY, "fig": E_FIG, "summary": E_FIG,
                     "meta": 0.005, "takeaway": 0.008, "thanks": 0.008,
                     "fig_top": E_FIG, "fig_bottom": E_FIG}[name]
            if cov < limit:
                err(f"第{i}页 {name} 区覆盖 {cov*100:.1f}%——区域空白（内容没放进去或没渲染）")
            elif name == "fig" and cov < W_FIG:
                warn(f"第{i}页图区覆盖 {cov*100:.1f}% 偏低，检查图是否过小/过淡")
            if name == "fig" and page_figures(pages[idx]) == 1:
                span = bbox_span(rows, h_px)
                line += f"(span {span*100:.0f}%)"
                if span < FIG_BBOX_MIN:
                    warn(f"第{i}页单图实际仅占图槽高度 {span*100:.0f}%——图偏小、版面空，"
                         f"补第二组图或换更合适的图")
        print(line)

    for w in warns:
        print(f"W  {w}")
    for e in errors:
        print(f"E  {e}")
    print(f"\n[inspect] 错误 {len(errors)} 项，警告 {len(warns)} 项")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
