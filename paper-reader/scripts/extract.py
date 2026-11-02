"""从论文 PDF 抽取全文、元信息、图表裁剪图和整页预览图。

用法: pr.sh extract <paper.pdf> --out <工作目录>

产出（都在 <工作目录>/extract/ 下）:
  text.md        带页码标记的全文（按版面块顺序，含图表标题），供通读
  outline.json   疑似章节标题（按字号/粗体判定），方便定位
  meta.json      题目候选、页数、PDF 元数据
  figures.json   图/表清单：id、标签、标题全文、页码、裁剪框、图片文件、尺寸
  figures/       每个图/表的裁剪 PNG（200 dpi）
  pages/         每页整页预览 PNG（110 dpi），用于核对裁剪是否完整、找不到图时整页兜底
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from pathlib import Path

import pymupdf

from common import die, log

CAPTION_RE = re.compile(
    r"^\s*(Figure|Fig\.?|FIGURE|FIG\.?|Table|TABLE|Algorithm|ALGORITHM|图|表)\s*"
    r"(\d+|[IVX]+)\s*([.:：．|]|\s{1,3}(?=[A-Z一-鿿]))",
)
FIG_DPI = 200
PAGE_DPI = 110


def block_text(b) -> str:
    return " ".join("".join(s["text"] for s in l["spans"]) for l in b.get("lines", [])).strip()


def block_size(b) -> float:
    sizes = [s["size"] for l in b.get("lines", []) for s in l["spans"] if s["text"].strip()]
    return statistics.median(sizes) if sizes else 0.0


def block_is_bold(b) -> bool:
    spans = [s for l in b.get("lines", []) for s in l["spans"] if s["text"].strip()]
    return bool(spans) and ("Bold" in spans[0]["font"] or "bold" in spans[0]["font"].lower()
                            or bool(spans[0]["flags"] & 16))


def body_font_size(doc) -> float:
    c = Counter()
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    c[round(s["size"], 0)] += len(s["text"])
    return c.most_common(1)[0][0] if c else 10.0


def is_caption(b, body_size: float) -> tuple[str, str] | None:
    t = block_text(b)
    m = CAPTION_RE.match(t)
    if not m:
        return None
    kind_word, num = m.group(1), m.group(2)
    # 正文中的 "Figure 3 shows ..." 没有句点/冒号，CAPTION_RE 已排除；
    # 再要求标题块要么加粗，要么字号不大于正文，避免把章节标题当图注。
    if block_size(b) > body_size + 1.5:
        return None
    k = kind_word.lower()
    if k.startswith(("fig", "图")):
        kind = "fig"
    elif k.startswith(("tab", "表")):
        kind = "tab"
    else:
        kind = "alg"
    return kind, num


def column_span(bbox, page_w) -> tuple[float, float]:
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
    # 半栏宽约占页宽 40%；正文行通常接近栏宽
    return avg_chars >= 25 and bw >= 0.3 * page_w


def find_regions(page, body_size, page_no):
    """返回本页 [(kind, num, caption_text, crop_rect)]。"""
    W, H = page.rect.width, page.rect.height
    d = page.get_text("dict")
    blocks = [b for b in d["blocks"] if b["type"] == 0 and block_text(b)]
    captions, body_blocks = [], []
    for b in blocks:
        c = is_caption(b, body_size)
        if c:
            captions.append((c, b))
            continue
        if is_body_block(b, body_size, W):
            body_blocks.append(b)

    # 页眉页脚范围
    top_limit = 40.0
    bot_limit = H - 40.0
    for b in blocks:
        y0, y1 = b["bbox"][1], b["bbox"][3]
        if y1 < 70 and block_size(b) < body_size:
            top_limit = max(top_limit, y1 + 2)
        if y0 > H - 70 and block_size(b) < body_size + 0.5 and len(block_text(b)) < 80:
            bot_limit = min(bot_limit, y0 - 2)

    # 图形元素：矢量绘制、位图、非正文文字
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
        # 表格标题通常在上方，图标题在下方；表格先往下找，找不到再往上
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
        caption = block_text(cb)
        results.append((kind, num, caption, crop, pymupdf.Rect(cb["bbox"])))
    return results


def guess_title(doc) -> str:
    page = doc[0]
    best, best_size = "", 0
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        sz = block_size(b)
        t = block_text(b)
        if sz > best_size and len(t) > 8 and b["bbox"][1] < page.rect.height * 0.5:
            best, best_size = t, sz
    return re.sub(r"\s+", " ", best)


def find_headings(doc, body_size):
    heads = []
    pat = re.compile(r"^(\d+(\.\d+)*\.?|[IVX]+\.|[A-Z]\.)?\s*[A-Z一-鿿]")
    for pno, page in enumerate(doc, 1):
        for b in page.get_text("dict")["blocks"]:
            if b["type"] != 0 or len(b["lines"]) > 2:
                continue
            t = block_text(b)
            if not (3 <= len(t) <= 90) or CAPTION_RE.match(t):
                continue
            sz = block_size(b)
            if (sz >= body_size + 1.0 or (block_is_bold(b) and sz >= body_size - 0.3)) and pat.match(t):
                heads.append({"page": pno, "size": round(sz, 1), "text": t})
    return heads


def main():
    ap = argparse.ArgumentParser()
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

    # ---- 全文
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

    # ---- 页面预览
    for pno, page in enumerate(doc, 1):
        page.get_pixmap(dpi=PAGE_DPI).save(out / "pages" / f"page{pno:02d}.png")

    # ---- 图表
    figures, seen = [], set()
    for pno, page in enumerate(doc, 1):
        for kind, num, caption, crop, cap_rect in find_regions(page, body, pno):
            fid = f"{kind}{num}"
            if fid in seen:
                fid = f"{fid}_p{pno}"
            seen.add(fid)
            entry = {"id": fid, "kind": kind, "label": caption.split(" ")[0] + " " + num,
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
    # 整页也登记成可引用的图（id: page3），供裁剪失败或需要展示整页时使用
    for pno in range(1, len(doc) + 1):
        figures.append({"id": f"page{pno}", "kind": "page", "label": f"Page {pno}",
                        "caption": f"原文第 {pno} 页", "page": pno,
                        "file": f"pages/page{pno:02d}.png"})
    (out / "figures.json").write_text(json.dumps(figures, ensure_ascii=False, indent=1), encoding="utf-8")

    meta = {"pdf": str(pdf.resolve()), "pages": len(doc), "title_guess": guess_title(doc),
            "pdf_metadata": {k: v for k, v in (doc.metadata or {}).items() if v},
            "body_font_size": body, "text_chars": total_chars}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "outline.json").write_text(json.dumps(find_headings(doc, body), ensure_ascii=False, indent=1),
                                      encoding="utf-8")

    n_fig = sum(1 for f in figures if f["kind"] in ("fig", "tab", "alg"))
    n_fail = sum(1 for f in figures if f["kind"] in ("fig", "tab", "alg") and f.get("crop") is None)
    log(f"完成：{len(doc)} 页，{total_chars} 字符，图/表/算法 {n_fig} 个（定位失败 {n_fail} 个）")
    log(f"输出目录：{out}")
    print(json.dumps({"extract_dir": str(out), "figures": n_fig, "failed": n_fail}, ensure_ascii=False))


if __name__ == "__main__":
    main()
