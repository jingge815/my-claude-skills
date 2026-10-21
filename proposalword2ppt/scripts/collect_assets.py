#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把素材里的图片和表格全部收出来，产出一份清单。

这一步不许跳过：不先看清素材里有什么，就只能一律自己画，申请书和团队资料里
现成的架构图、性能曲线、厂商标识、指标表就全废了。

支持 .docx / .pptx / .pdf（PDF 需要 pdfimages 或 PyMuPDF，缺了就只记页数并提示）。
图片按包内实际文件数核对，数量不一致就报出来，不静默丢。

用法：
    python3 collect_assets.py --input 申请书.docx 团队汇报.pptx --out-dir work/assets
    python3 collect_assets.py --input 素材目录/ --out-dir work/assets
输出：
    <out-dir>/images/      抽出的图片，文件名带来源与序号
    <out-dir>/tables/      抽出的表格，Markdown
    <out-dir>/index.json   机器可读清单（供 audit_process.py 校验）
    <out-dir>/清单.md      人读清单：每张图的像素、来源、在哪一节
"""

import argparse
import json
import os
import re
import shutil
import sys
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
V = "{urn:schemas-microsoft-com:vml}"
O = "{urn:schemas-microsoft-com:office:office}"

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".emf", ".wmf", ".svg")


def image_size(path):
    try:
        from PIL import Image
    except ImportError:
        return (None, None)
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (None, None)


def effective_dpi(px, cm):
    """这张图铺到 cm 宽时的有效 DPI。低于 110 投影下会糊。"""
    if not px or not cm:
        return None
    return round(px / (cm / 2.54), 1)


def para_text(p):
    out = []
    for node in p.iter():
        if node.tag == W + "t":
            out.append(node.text or "")
        elif node.tag == W + "tab":
            out.append("\t")
    return re.sub(r"[ \t]+", " ", "".join(out)).strip()


HEADING_RE = re.compile(r"^(?:heading|标题|Heading)\s*([1-9])$", re.I)


def is_heading(p):
    """是不是标题段。

    和 extract_doc.py 的 para_level 用同一套判断：很多申请书模板的标题样式名
    不叫 heading N，而是 a0 / ad 这类自动名，只能靠 w:outlineLvl 认出来。
    只看样式名会把全文的标题判成 0 个，图就全挂不上章节。
    """
    ppr = p.find(W + "pPr")
    if ppr is None:
        return False
    style = ppr.find(W + "pStyle")
    if style is not None:
        val = (style.get(W + "val") or "").strip()
        if HEADING_RE.match(val) or val.lower() in ("title", "标题"):
            return True
    outline = ppr.find(W + "outlineLvl")
    if outline is not None:
        try:
            return int(outline.get(W + "val")) <= 8
        except (TypeError, ValueError):
            return False
    return False


def docx_tables_and_images(path, out_dir, stem):
    """抽 docx：表格转 Markdown，图片按 blip 和 VML 两种写法都抽。"""
    images, tables = [], []
    z = zipfile.ZipFile(path)
    names = z.namelist()
    media = [n for n in names if n.startswith("word/media/")
             and os.path.splitext(n)[1].lower() in IMAGE_EXT]
    rels = {}
    try:
        rel_xml = ET.fromstring(z.read("word/_rels/document.xml.rels"))
        for rel in rel_xml:
            rid = rel.get("Id")
            tgt = rel.get("Target") or ""
            if rid and "media/" in tgt:
                rels[rid] = "word/" + tgt.lstrip("/").replace("../", "")
    except KeyError:
        pass

    # 图片在文档中的位置：记下它前面最近的一个标题，知道它属于哪一节
    doc = ET.fromstring(z.read("word/document.xml"))
    body = doc.find(W + "body")
    section = ""
    order = []
    if body is not None:
        for child in body.iter():
            if child.tag == W + "p":
                txt = para_text(child)
                if txt and is_heading(child):
                    section = txt
                for node in child.iter():
                    rid = None
                    if node.tag == A + "blip":
                        rid = node.get(R + "embed") or node.get(R + "link")
                    elif node.tag == V + "imagedata":
                        rid = node.get(R + "id")
                    if rid and rid in rels:
                        order.append((rels[rid], section))

    seen = {}
    os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
    for i, name in enumerate(media, 1):
        ext = os.path.splitext(name)[1].lower()
        out_name = "%s_img%02d%s" % (stem, i, ext)
        out_path = os.path.join(out_dir, "images", out_name)
        with open(out_path, "wb") as fh:
            fh.write(z.read(name))
        px = image_size(out_path)
        sec = next((s for n, s in order if n == name), "")
        seen[name] = True
        images.append({
            "file": os.path.relpath(out_path, out_dir),
            "source": os.path.basename(path),
            "section": sec,
            "px_w": px[0], "px_h": px[1],
            "kind": "未分类",
        })
    missed = [n for n in media if n not in seen]
    if missed:
        print("  [警告] %s 有 %d 张图未抽出：%s" % (path, len(missed), missed[:3]), file=sys.stderr)

    os.makedirs(os.path.join(out_dir, "tables"), exist_ok=True)
    t_i = 0
    if body is not None:
        for tbl in body.iter(W + "tbl"):
            rows = []
            for tr in tbl.findall(W + "tr"):
                cells = [" ".join(para_text(p) for p in tc.iter(W + "p")).strip()
                         for tc in tr.findall(W + "tc")]
                if any(cells):
                    rows.append(cells)
            if not rows:
                continue
            t_i += 1
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            md = ["| " + " | ".join(rows[0]) + " |",
                  "|" + "|".join(["---"] * width) + "|"]
            md += ["| " + " | ".join(r) + " |" for r in rows[1:]]
            out_name = "%s_tbl%02d.md" % (stem, t_i)
            out_path = os.path.join(out_dir, "tables", out_name)
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(md) + "\n")
            tables.append({
                "file": os.path.relpath(out_path, out_dir),
                "source": os.path.basename(path),
                "rows": len(rows), "cols": width,
                "head": " | ".join(rows[0])[:80],
            })
    return images, tables, len(media)


def pptx_tables_and_images(path, out_dir, stem):
    """抽 pptx：图片取全部部件的 media（含版式与母版），表格读 a:tbl。"""
    images, tables = [], []
    z = zipfile.ZipFile(path)
    media = [n for n in z.namelist() if n.startswith("ppt/media/")
             and os.path.splitext(n)[1].lower() in IMAGE_EXT]
    os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
    for i, name in enumerate(media, 1):
        ext = os.path.splitext(name)[1].lower()
        out_name = "%s_img%02d%s" % (stem, i, ext)
        out_path = os.path.join(out_dir, "images", out_name)
        with open(out_path, "wb") as fh:
            fh.write(z.read(name))
        px = image_size(out_path)
        images.append({
            "file": os.path.relpath(out_path, out_dir),
            "source": os.path.basename(path),
            "section": "",
            "px_w": px[0], "px_h": px[1],
            "kind": "未分类",
        })

    os.makedirs(os.path.join(out_dir, "tables"), exist_ok=True)
    t_i = 0
    slides = sorted(n for n in z.namelist()
                    if re.match(r"ppt/slides/slide\d+\.xml$", n))
    for sname in slides:
        try:
            root = ET.fromstring(z.read(sname))
        except ET.ParseError:
            continue
        for tbl in root.iter(A + "tbl"):
            rows = []
            for tr in tbl.findall(A + "tr"):
                cells = []
                for tc in tr.findall(A + "tc"):
                    cells.append(" ".join(t.text or "" for t in tc.iter(A + "t")).strip())
                if any(cells):
                    rows.append(cells)
            if not rows:
                continue
            t_i += 1
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            md = ["| " + " | ".join(rows[0]) + " |",
                  "|" + "|".join(["---"] * width) + "|"]
            md += ["| " + " | ".join(r) + " |" for r in rows[1:]]
            out_name = "%s_tbl%02d.md" % (stem, t_i)
            out_path = os.path.join(out_dir, "tables", out_name)
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(md) + "\n")
            tables.append({
                "file": os.path.relpath(out_path, out_dir),
                "source": "%s:%s" % (os.path.basename(path), os.path.basename(sname)),
                "rows": len(rows), "cols": width,
                "head": " | ".join(rows[0])[:80],
            })
    return images, tables, len(media)


def pdf_images(path, out_dir, stem):
    """抽 PDF 图片。优先 PyMuPDF，其次 pdfimages；都没有就提示用 Read 看。"""
    images = []
    os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
    try:
        import fitz  # PyMuPDF
    except ImportError:
        fitz = None
    if fitz is not None:
        doc = fitz.open(path)
        n = 0
        for pno in range(len(doc)):
            for info in doc[pno].get_images(full=True):
                xref = info[0]
                pix = fitz.Pixmap(doc, xref)
                if pix.n > 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                n += 1
                out_name = "%s_img%02d.png" % (stem, n)
                out_path = os.path.join(out_dir, "images", out_name)
                pix.save(out_path)
                images.append({
                    "file": os.path.relpath(out_path, out_dir),
                    "source": "%s:p%d" % (os.path.basename(path), pno + 1),
                    "section": "", "px_w": pix.width, "px_h": pix.height,
                    "kind": "未分类",
                })
        return images, [], len(images)
    if shutil.which("pdfimages"):
        prefix = os.path.join(out_dir, "images", stem)
        os.system('pdfimages -png "%s" "%s" >/dev/null 2>&1' % (path, prefix))
        for f in sorted(os.listdir(os.path.join(out_dir, "images"))):
            if f.startswith(os.path.basename(prefix)) and f.endswith(".png"):
                fp = os.path.join(out_dir, "images", f)
                px = image_size(fp)
                images.append({
                    "file": os.path.relpath(fp, out_dir),
                    "source": os.path.basename(path), "section": "",
                    "px_w": px[0], "px_h": px[1], "kind": "未分类",
                })
        return images, [], len(images)
    print("  [提示] %s：没有 PyMuPDF 也没有 pdfimages，PDF 里的图没抽。"
          "用 Read 工具按页看，需要的图手工截出来放进 images/" % path, file=sys.stderr)
    return [], [], 0


def collect(paths, out_dir):
    images, tables, counts = [], [], []
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        stem = re.sub(r"[^\w一-鿿]+", "_", os.path.splitext(os.path.basename(path))[0])[:24]
        print("读素材：%s" % path)
        if ext == ".docx":
            im, tb, n = docx_tables_and_images(path, out_dir, stem)
        elif ext == ".pptx":
            im, tb, n = pptx_tables_and_images(path, out_dir, stem)
        elif ext == ".pdf":
            im, tb, n = pdf_images(path, out_dir, stem)
        elif ext in IMAGE_EXT:
            os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
            dst = os.path.join(out_dir, "images", os.path.basename(path))
            shutil.copy2(path, dst)
            px = image_size(dst)
            im = [{"file": os.path.relpath(dst, out_dir), "source": os.path.basename(path),
                   "section": "", "px_w": px[0], "px_h": px[1], "kind": "未分类"}]
            tb, n = [], 1
        else:
            print("  跳过（不支持的类型）")
            continue
        images += im
        tables += tb
        counts.append({"source": os.path.basename(path), "media_in_package": n,
                       "images_extracted": len(im), "tables_extracted": len(tb)})
        print("  图 %d 张、表 %d 个" % (len(im), len(tb)))
    return images, tables, counts


def main():
    ap = argparse.ArgumentParser(description="收集素材里的图片与表格，产出清单")
    ap.add_argument("--input", nargs="+", required=True, help="素材文件或目录")
    ap.add_argument("--out-dir", required=True, help="输出目录")
    args = ap.parse_args()

    paths = []
    for item in args.input:
        if os.path.isdir(item):
            for root, _dirs, files in os.walk(item):
                for f in sorted(files):
                    if os.path.splitext(f)[1].lower() in (".docx", ".pptx", ".pdf") + IMAGE_EXT:
                        paths.append(os.path.join(root, f))
        elif os.path.exists(item):
            paths.append(item)
        else:
            print("找不到：%s" % item, file=sys.stderr)
    if not paths:
        raise SystemExit("没有可读素材")

    os.makedirs(args.out_dir, exist_ok=True)
    images, tables, counts = collect(paths, args.out_dir)

    index = {"images": images, "tables": tables, "counts": counts}
    with open(os.path.join(args.out_dir, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)

    lines = ["# 素材清单", "",
             "共 %d 张图、%d 个表。`kind` 需要人工过一遍：标成"
             "厂商标识 / 论文或证明 / 性能曲线 / 架构图 / 指标表 / 经费表 / 进度表 / 用不上。"
             % (len(images), len(tables)),
             "", "## 图片", "",
             "| 文件 | 来源 | 所属章节 | 像素 | 铺到 17 cm 的 DPI | 分类 |",
             "|---|---|---|---|---|---|"]
    for im in images:
        dpi = effective_dpi(im["px_w"], 17.0)
        lines.append("| %s | %s | %s | %s×%s | %s | %s |" % (
            im["file"], im["source"], im["section"] or "—",
            im["px_w"] or "?", im["px_h"] or "?",
            ("%.0f" % dpi) if dpi else "?", im["kind"]))
    lines += ["", "## 表格", "", "| 文件 | 来源 | 行×列 | 表头 |", "|---|---|---|---|"]
    for tb in tables:
        lines.append("| %s | %s | %d×%d | %s |" % (
            tb["file"], tb["source"], tb["rows"], tb["cols"], tb["head"]))
    lines += ["", "## 抽取核对", "", "| 来源 | 包内图片文件 | 抽出图片 | 抽出表格 |", "|---|---|---|---|"]
    for c in counts:
        flag = "" if c["media_in_package"] == c["images_extracted"] else "  ← 不一致，查漏"
        lines.append("| %s | %d | %d | %d |%s" % (
            c["source"], c["media_in_package"], c["images_extracted"],
            c["tables_extracted"], flag))
    with open(os.path.join(args.out_dir, "清单.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print("\n已写出：%s/index.json 与 清单.md" % args.out_dir)
    print("共 %d 张图、%d 个表。下一步：逐个看图，把 index.json 里的 kind 标好，"
          "再做图表规划（plan_figures.py）。" % (len(images), len(tables)))


if __name__ == "__main__":
    main()
