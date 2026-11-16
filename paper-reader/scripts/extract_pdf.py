"""从论文 PDF 抽取全文、整页预览图、图表裁剪图。

用法: .venv/bin/python scripts/extract_pdf.py <论文.pdf> --out <工作目录>

产出（都在 <工作目录>/extract/ 下）:
  text.md        带页码标记的全文（按版面块顺序），供通读与数字溯源
  cover.png      第 1 页顶部封面裁剪（题目+作者），供总结引用为图1
  figures.json   图/表清单：id、标题全文、页码、裁剪文件、宽高比（首个条目为 cover）
  figures/       每个图/表的裁剪 PNG（200 dpi）
  pages/         每页整页预览 PNG（110 dpi），用于核对裁剪、扫描版兜底
  meta.json      题目候选、页数、PDF 元数据、link_candidates（第 1 页扫出的 arXiv/DOI 候选外链）
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

import pymupdf

CAPTION_RE = re.compile(
    r"^\s*(Figure|Fig\.?|FIGURE|FIG\.?|Table|TABLE|Algorithm|ALGORITHM|图|表)\s*"
    r"(\d+|[IVX]+)\s*([.:：．|]|\s{1,3}(?=[A-Z一-鿿]))",
)
FIG_DPI = 200
PAGE_DPI = 110


def die(msg: str, code: int = 1):
    print(f"[extract] 错误: {msg}", file=sys.stderr)
    sys.exit(code)


def log(msg: str):
    print(f"[extract] {msg}", file=sys.stderr, flush=True)


def block_text(b) -> str:
    return " ".join("".join(s["text"] for s in l["spans"]) for l in b.get("lines", [])).strip()


def block_size(b) -> float:
    sizes = [s["size"] for l in b.get("lines", []) for s in l["spans"] if s["text"].strip()]
    return statistics.median(sizes) if sizes else 0.0


def body_font_size(doc) -> float:
    c = Counter()
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    c[round(s["size"], 0)] += len(s["text"])
    return c.most_common(1)[0][0] if c else 10.0


def is_caption(b, body_size: float):
    t = block_text(b)
    m = CAPTION_RE.match(t)
    if not m:
        return None
    # 排除正文中的 "Figure 3 shows ..."；再要求标题块字号不大于正文，避免误抓章节标题
    if block_size(b) > body_size + 1.5:
        return None
    k = m.group(1).lower()
    if k.startswith(("fig", "图")):
        kind = "fig"
    elif k.startswith(("tab", "表")):
        kind = "tab"
    else:
        kind = "alg"
    return kind, m.group(2)


def column_span(bbox, page_w):
    """图注所在栏的水平范围。跨过页面中线的图注视为通栏。"""
    mid = page_w / 2
    x0, _, x1, _ = bbox
    margin = 36
    if x0 < mid - 8 and x1 > mid + 8:
        return margin, page_w - margin
    if x1 <= mid + 8:
        return margin, mid - 4
    return mid + 4, page_w - margin


def overlaps_x(a0, a1, b0, b1, min_frac=0.3) -> bool:
    inter = min(a1, b1) - max(a0, b0)
    return inter > min_frac * min(a1 - a0, b1 - b0)


def is_body_block(b, body_size, page_w) -> bool:
    """正文段落：字号接近正文，且行足够长。坐标轴刻度、图例等短行块不算正文。"""
    if abs(block_size(b) - body_size) > 0.8:
        return False
    lines = ["".join(s["text"] for s in l["spans"]).strip() for l in b["lines"]]
    lines = [l for l in lines if l]
    if not lines:
        return False
    avg_chars = sum(len(l) for l in lines) / len(lines)
    bw = b["bbox"][2] - b["bbox"][0]
    return avg_chars >= 25 and bw >= 0.3 * page_w


def find_regions(page, body_size):
    """返回本页 [(kind, num, caption_text, crop_rect)]。"""
    W, H = page.rect.width, page.rect.height
    d = page.get_text("dict")
    blocks = [b for b in d["blocks"] if b["type"] == 0 and block_text(b)]
    captions, body_blocks = [], []
    for b in blocks:
        c = is_caption(b, body_size)
        if c:
            captions.append((c, b))
        elif is_body_block(b, body_size, W):
            body_blocks.append(b)

    # 页眉页脚范围
    top_limit, bot_limit = 40.0, H - 40.0
    for b in blocks:
        y0, y1 = b["bbox"][1], b["bbox"][3]
        if y1 < 70 and block_size(b) < body_size:
            top_limit = max(top_limit, y1 + 2)
        if y0 > H - 70 and block_size(b) < body_size + 0.5 and len(block_text(b)) < 80:
            bot_limit = min(bot_limit, y0 - 2)

    # 图形元素：矢量绘制、位图、非正文文字（图例、坐标刻度等）
    graphics = []
    for dr in page.get_drawings():
        r = dr["rect"]
        if r.width * r.height > 4 or r.width > 20 or r.height > 20:
            graphics.append(pymupdf.Rect(r))
    for info in page.get_image_info():
        graphics.append(pymupdf.Rect(info["bbox"]))
    cap_ids = {id(b) for _, b in captions}
    body_ids = {id(b) for b in body_blocks}
    for b in blocks:
        if id(b) not in cap_ids and id(b) not in body_ids:
            graphics.append(pymupdf.Rect(b["bbox"]))

    results = []
    for (kind, num), cb in captions:
        cx0, cy0, cx1, cy1 = cb["bbox"]
        sx0, sx1 = column_span(cb["bbox"], W)
        # 表格标题通常在上方，图标题在下方；各先往惯例方向找
        directions = ["down", "up"] if kind == "tab" else ["up", "down"]
        crop = None
        for direction in directions:
            if direction == "up":
                limit = top_limit
                for b in body_blocks + [c for _, c in captions if c is not cb]:
                    bx0, by0, bx1, by1 = b["bbox"]
                    if by1 <= cy0 + 1 and overlaps_x(sx0, sx1, bx0, bx1):
                        limit = max(limit, by1)
                region = pymupdf.Rect(sx0, limit, sx1, cy0 - 1)
            else:
                limit = bot_limit
                for b in body_blocks + [c for _, c in captions if c is not cb]:
                    bx0, by0, bx1, by1 = b["bbox"]
                    if by0 >= cy1 - 1 and overlaps_x(sx0, sx1, bx0, bx1):
                        limit = min(limit, by0)
                region = pymupdf.Rect(sx0, cy1 + 1, sx1, limit)
            if region.height < 15:
                continue
            inside = [g for g in graphics if region.contains(g) or (
                g.intersects(region) and (g & region).get_area() > 0.6 * max(g.get_area(), 1))]
            if not inside:
                continue
            u = pymupdf.Rect(inside[0])
            for g in inside[1:]:
                u |= g
            if u.height < 12 or u.width < 30:
                continue
            crop = (u + (-4, -4, 4, 4)) & page.rect
            break
        results.append((kind, num, block_text(cb), crop))
    return results


def guess_title(doc) -> str:
    page = doc[0]
    best, best_size = "", 0
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        sz, t = block_size(b), block_text(b)
        if sz > best_size and len(t) > 8 and b["bbox"][1] < page.rect.height * 0.5:
            best, best_size = t, sz
    return re.sub(r"\s+", " ", best)


def cover_region(page) -> pymupdf.Rect:
    """第 1 页封面区域：页面顶部到摘要起点（含题目与作者），找不到摘要时取上部 45%。"""
    H = page.rect.height
    limit = H * 0.45
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        t = block_text(b)
        if b["bbox"][1] > H * 0.15 and re.match(r"(?i)^abstract\b|^摘\s*要", t):
            limit = min(limit, b["bbox"][1] - 6)
            break
    limit = max(min(limit, H * 0.6), H * 0.12)
    return pymupdf.Rect(page.rect.x0, page.rect.y0, page.rect.x1, limit)


def link_candidates(page) -> list:
    """从第 1 页文本扫出本文的候选外链（arXiv 编号 / DOI）。只扫第 1 页，
    避免抓到参考文献里别人工作的 arXiv/DOI；结果供人工核实归属本文后再使用。"""
    text = page.get_text()
    found = []

    def add(u):
        if u not in found:
            found.append(u)

    for m in re.finditer(r"arXiv:\s*(\d{4}\.\d{4,5})(?:v\d+)?", text):
        add(f"https://arxiv.org/abs/{m.group(1)}")
    for m in re.finditer(r"(?i)\b(10\.\d{4,9}/[^\s\"'>,;)\]]+)", text):
        add(f"https://doi.org/{m.group(1).rstrip('.,;')}")
    return found[:5]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True, help="工作目录，产物写到 <out>/extract/")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.exists():
        die(f"找不到 PDF: {pdf}")
    out = Path(args.out) / "extract"
    (out / "figures").mkdir(parents=True, exist_ok=True)
    (out / "pages").mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(pdf)
    if doc.needs_pass:
        die("PDF 有密码保护，无法解析")
    body = body_font_size(doc)

    # ---- 全文（带页码标记）
    lines = [f"# {pdf.name}\n"]
    total_chars = 0
    for pno, page in enumerate(doc, 1):
        lines.append(f"\n\n<!-- page {pno} -->\n")
        for b in page.get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            t = block_text(b)
            if t:
                total_chars += len(t)
                lines.append(t + "\n")
    (out / "text.md").write_text("\n".join(lines), encoding="utf-8")
    if total_chars < 500 * max(1, len(doc) // 4):
        log("警告：可提取文字很少，可能是扫描版 PDF。请改为查看 pages/ 下的整页图片阅读。")

    # ---- 整页预览
    for pno, page in enumerate(doc, 1):
        page.get_pixmap(dpi=PAGE_DPI).save(out / "pages" / f"page{pno:02d}.png")

    # ---- 图表裁剪
    figures, seen = [], set()
    for pno, page in enumerate(doc, 1):
        for kind, num, caption, crop in find_regions(page, body):
            fid = f"{kind}{num}"
            if fid in seen:
                fid = f"{fid}_p{pno}"
            seen.add(fid)
            entry = {"id": fid, "kind": kind, "label": f"{caption.split(' ')[0]} {num}",
                     "caption": re.sub(r"\s+", " ", caption), "page": pno}
            if crop is None:
                entry.update({"file": f"pages/page{pno:02d}.png", "crop": None,
                              "note": "未能定位图区域，已用整页兜底，请人工确认"})
            else:
                fname = f"figures/{fid}.png"
                pix = page.get_pixmap(dpi=FIG_DPI, clip=crop)
                pix.save(out / fname)
                entry.update({"file": fname, "crop": [round(v, 1) for v in crop],
                              "width": pix.width, "height": pix.height,
                              "aspect": round(pix.width / pix.height, 3)})
            figures.append(entry)
    # ---- 第 1 页封面（题目+作者），总结固定引用为全文图1
    pix = doc[0].get_pixmap(dpi=FIG_DPI, clip=cover_region(doc[0]))
    pix.save(out / "cover.png")
    figures.insert(0, {"id": "cover", "kind": "cover", "label": "Cover",
                       "caption": "论文第 1 页封面（题目与作者信息）", "page": 1,
                       "file": "cover.png", "width": pix.width, "height": pix.height,
                       "aspect": round(pix.width / pix.height, 3)})

    # 整页也登记成可引用的图（id: page3），供裁剪失败或需要展示整页时使用
    for pno in range(1, len(doc) + 1):
        figures.append({"id": f"page{pno}", "kind": "page", "label": f"Page {pno}",
                        "caption": f"原文第 {pno} 页", "page": pno,
                        "file": f"pages/page{pno:02d}.png"})
    (out / "figures.json").write_text(
        json.dumps(figures, ensure_ascii=False, indent=1), encoding="utf-8")

    meta = {"pdf": str(pdf.resolve()), "pages": len(doc), "title_guess": guess_title(doc),
            "link_candidates": link_candidates(doc[0]),
            "pdf_metadata": {k: v for k, v in (doc.metadata or {}).items() if v},
            "text_chars": total_chars}
    (out / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    n_fig = sum(1 for f in figures if f["kind"] in ("fig", "tab", "alg"))
    n_fail = sum(1 for f in figures if f["kind"] in ("fig", "tab", "alg") and f.get("crop") is None)
    log(f"完成：{len(doc)} 页，{total_chars} 字符，图/表/算法 {n_fig} 个（定位失败 {n_fail} 个），封面 cover.png，候选外链 {meta['link_candidates']}")
    log(f"输出目录：{out}")
    print(json.dumps({"extract_dir": str(out), "figures": n_fig, "failed": n_fail,
                      "pages": len(doc)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
