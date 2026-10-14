#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把项目申请书（.docx）抽成带层级标记的 Markdown，供章节映射使用。

申请书里的「研究内容」「经费预算」「进度安排」常以表格承载，因此表格单独渲染为
Markdown 表格，不丢信息。只依赖 Python 标准库。内嵌图片会抽到与输出 Markdown 同名的 `_media/` 目录，
并在原文位置插入 `![申请书配图N](path)`，供后续 PPT 图表槽复用。

用法：
    python3 extract_doc.py --input 申请书.docx --out 申请书.md
    python3 extract_doc.py --input 申请书.docx          # 结果打印到标准输出
.pdf 不支持：PDF 用 Read 工具直接读（Read 支持 pages 参数），不要用本脚本。
"""

import argparse
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

HEADING_RE = re.compile(r"^(?:heading|标题|Heading)\s*([1-9])$", re.I)


def para_text(p):
    """取段落纯文本（含 w:tab / w:br 的近似处理）。"""
    out = []
    for node in p.iter():
        tag = node.tag
        if tag == W + "t":
            out.append(node.text or "")
        elif tag == W + "tab":
            out.append("\t")
        elif tag in (W + "br", W + "cr"):
            out.append(" ")
    return re.sub(r"[ \t]+", " ", "".join(out)).strip()


def para_level(p):
    """判断段落是否为标题，返回 1–9；正文返回 None。"""
    ppr = p.find(W + "pPr")
    if ppr is None:
        return None
    style = ppr.find(W + "pStyle")
    if style is not None:
        val = style.get(W + "val") or ""
        m = HEADING_RE.match(val.strip())
        if m:
            return int(m.group(1))
        if val.strip().lower() in ("title", "标题"):
            return 1
    outline = ppr.find(W + "outlineLvl")
    if outline is not None:
        try:
            return int(outline.get(W + "val")) + 1
        except (TypeError, ValueError):
            return None
    return None


def table_rows(tbl):
    rows = []
    for tr in tbl.findall(W + "tr"):
        cells = []
        for tc in tr.findall(W + "tc"):
            text = " ".join(
                para_text(p) for p in tc.findall(W + "p") if para_text(p)
            )
            cells.append(text.replace("|", "／"))
        rows.append(cells)
    return rows


def render_table(rows):
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return lines


def load_rels(zf):
    try:
        xml = zf.read("word/_rels/document.xml.rels")
    except KeyError:
        return {}
    root = ET.fromstring(xml)
    rels = {}
    for rel in root:
        rid = rel.get("Id")
        target = rel.get("Target")
        if rid and target:
            if not target.startswith("word/"):
                target = "word/" + target.lstrip("/")
            rels[rid] = target.replace("\\", "/")
    return rels


def para_blips(p):
    embeds = []
    for blip in p.iter(A + "blip"):
        embed = blip.get(R + "embed")
        if embed:
            embeds.append(embed)
    return embeds


def save_image(zf, target, dest_dir, index):
    name = os.path.basename(target)
    ext = os.path.splitext(name)[1].lower() or ".bin"
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".wmf", ".emf", ".tif", ".tiff", ".bmp"):
        ext = ".bin"
    out_name = "image%d%s" % (index, ext)
    dest = os.path.join(dest_dir, out_name)
    try:
        data = zf.read(target)
    except KeyError:
        alt = target.split("/", 1)[-1]
        try:
            data = zf.read("word/" + alt)
        except KeyError:
            return None
    with open(dest, "wb") as fh:
        fh.write(data)
    return dest


def extract(path, media_dir=None):
    zf = zipfile.ZipFile(path)
    try:
        xml = zf.read("word/document.xml")
    except KeyError:
        raise SystemExit("不是有效的 .docx（缺少 word/document.xml）：%s" % path)
    root = ET.fromstring(xml)
    body = root.find(W + "body")
    if body is None:
        raise SystemExit("docx 结构异常：找不到 w:body")

    rels = load_rels(zf)
    saved, img_index = {}, 0
    if media_dir:
        os.makedirs(media_dir, exist_ok=True)

    def dump_blips(el):
        refs = []
        if media_dir is None:
            return refs
        for rid in para_blips(el):
            target = rels.get(rid)
            if not target:
                continue
            if target not in saved:
                img_index_holder[0] += 1
                dest = save_image(zf, target, media_dir, img_index_holder[0])
                saved[target] = dest
            dest = saved.get(target)
            if dest:
                refs.append(dest)
        return refs

    img_index_holder = [img_index]
    lines, headings = [], []
    for child in body:
        if child.tag == W + "p":
            text = para_text(child)
            level = para_level(child)
            images = dump_blips(child)
            if level and text:
                level = min(max(level, 1), 6)
                lines.append("")
                lines.append("#" * level + " " + text)
                headings.append((level, text))
            elif text:
                lines.append(text)
                lines.append("")
            for dest in images:
                n = os.path.basename(dest)
                lines.append("![申请书配图 %s](%s)" % (n, dest))
                lines.append("")
        elif child.tag == W + "tbl":
            lines.append("")
            lines.extend(render_table(table_rows(child)))
            lines.append("")
            for dest in dump_blips(child):
                n = os.path.basename(dest)
                lines.append("![申请书配图 %s](%s)" % (n, dest))
                lines.append("")

    if saved:
        lines.append("")
        lines.append("## 申请书内嵌图片")
        for dest in saved.values():
            if dest:
                lines.append("- %s" % dest)
        lines.append("")

    doc = "\n".join(lines).strip() + "\n"
    return doc, headings, [p for p in saved.values() if p]


def main():
    ap = argparse.ArgumentParser(description="抽取 .docx 文本与章节标题")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", default=None, help="输出 Markdown 路径；缺省打印到标准输出")
    ap.add_argument("--media-dir", default=None, help="内嵌图片输出目录；默认与 --out 同名的 _media/")
    ap.add_argument("--headings-only", action="store_true", help="只列章节标题，便于做章节映射")
    args = ap.parse_args()

    media_dir = None
    if args.out and not args.headings_only:
        media_dir = args.media_dir or (os.path.splitext(args.out)[0] + "_media")

    doc, headings, images = extract(args.input, media_dir=media_dir)

    if args.headings_only:
        payload = "\n".join("%s%s" % ("  " * (lv - 1), t) for lv, t in headings) + "\n"
    else:
        payload = doc

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        print("已写出：%s（正文字符 %d，章节标题 %d 个，图片 %d 张）" % (args.out, len(doc), len(headings), len(images)))
        if images:
            print("图片目录：%s" % media_dir)
        if not args.headings_only:
            print("章节标题：")
            for lv, t in headings:
                print("  %s%s" % ("  " * (lv - 1), t))
    else:
        sys.stdout.write(payload)


if __name__ == "__main__":
    main()
