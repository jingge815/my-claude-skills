"""把论文总结.md 转换为排版规范的中文 Word 文档。

用法: .venv/bin/python scripts/build_docx.py <论文总结.md> [--out 输出.docx]

支持的 md 子集（与 SKILL.md 约定的格式一一对应）:
  # 标题            文档标题，黑体三号居中
  ## 一.xxx        一级节标题，黑体四号
  ### 5.1 xxx      二级小节标题，黑体小四
  普通段落          以全角空格「　　」开头的正文，转换为首行缩进2字符
  ![](figs/x.png)  居中插图，自动按版心宽度/高度上限缩放
  图N 题目         居中图题（图片下一行）
  表N 题目         居中表题（表格上一行）
  | a | b |        GFM 表格，Table Grid 样式，表头加粗浅灰底
  1. / - 条目      列表条目（附录等），悬挂缩进
  > 引用           灰色引用块
  **加粗**         行内加粗
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image

BODY_EA, BODY_LATIN = "宋体", "Times New Roman"
HEAD_EA, HEAD_LATIN = "黑体", "Arial"
TEXT_WIDTH_CM = 16.0     # A4 21cm - 左右 2.5cm
MAX_IMG_H_CM = 19.0
CAPTION_PT = 10.5        # 五号
GRAY = RGBColor(0x59, 0x59, 0x59)

BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
IMG_RE = re.compile(r"^!\[[^\]]*\]\(([^)]+)\)\s*$")
FIG_CAP_RE = re.compile(r"^图\s*\d+")
TAB_CAP_RE = re.compile(r"^表\s*\d+")


def die(msg: str):
    print(f"[docx] 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def set_font(run, size, *, ea=BODY_EA, latin=BODY_LATIN, bold=False, color=None):
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.name = latin
    if color is not None:
        f.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for k in ("w:ascii", "w:hAnsi", "w:cs"):
        rfonts.set(qn(k), latin)
    rfonts.set(qn("w:eastAsia"), ea)


def add_rich(p, text, size, *, bold=False, color=None, ea=BODY_EA, latin=BODY_LATIN):
    """渲染含 **加粗** 的行内文本。"""
    pos = 0
    for m in BOLD_RE.finditer(text):
        if m.start() > pos:
            set_font(p.add_run(text[pos:m.start()]), size, ea=ea, latin=latin, bold=bold, color=color)
        set_font(p.add_run(m.group(1)), size, ea=ea, latin=latin, bold=True, color=color)
        pos = m.end()
    if pos < len(text):
        set_font(p.add_run(text[pos:]), size, ea=ea, latin=latin, bold=bold, color=color)


def indent_2chars(p, size_pt):
    """首行缩进2字符：优先用 Word 的 firstLineChars（随字号自适应），同时给磅值兜底。"""
    ppr = p._p.get_or_add_pPr()
    ind = ppr.find(qn("w:ind"))
    if ind is None:
        ind = OxmlElement("w:ind")
        ppr.append(ind)
    ind.set(qn("w:firstLineChars"), "200")
    ind.set(qn("w:firstLine"), str(int(size_pt * 20 * 2)))


def shade(cell, fill):
    tcpr = cell._element.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcpr.append(shd)


def add_caption(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(10)
    p.paragraph_format.line_spacing = 1.2
    add_rich(p, text, CAPTION_PT, bold=True)


def add_image(doc, path: Path):
    if not path.exists():
        die(f"图片不存在: {path}")
    with Image.open(path) as im:
        w_px, h_px = im.size
    w_cm = TEXT_WIDTH_CM
    h_cm = w_cm * h_px / w_px
    if h_cm > MAX_IMG_H_CM:          # 过长的图按高度上限缩放
        h_cm = MAX_IMG_H_CM
        w_cm = h_cm * w_px / h_px
    if w_px < 700:                   # 分辨率小的图不放大，按 96dpi 原尺寸（上限版心宽）
        nat_cm = w_px / 96 * 2.54
        w_cm = min(w_cm, nat_cm)
        h_cm = w_cm * h_px / w_px
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.add_run().add_picture(str(path), width=Cm(w_cm), height=Cm(h_cm))
    p.paragraph_format.space_after = Pt(2)


def add_table(doc, rows):
    header = [c.strip() for c in rows[0].strip("|").split("|")]
    body = []
    for r in rows[2:]:
        cells = [c.strip() for c in r.strip("|").split("|")]
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        body.append(cells)
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(header):
        c = t.rows[0].cells[i]
        c.text = ""
        add_rich(c.paragraphs[0], h, CAPTION_PT, bold=True)
        c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        shade(c, "EDEDED")
    for r in body:
        cells = t.add_row().cells
        for i, v in enumerate(r[:len(header)]):
            cells[i].text = ""
            add_rich(cells[i].paragraphs[0], v, CAPTION_PT)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def build(md_path: Path, out: Path):
    lines = md_path.read_text(encoding="utf-8").splitlines()
    doc = Document()

    st = doc.styles["Normal"]
    st.font.size = Pt(12)          # 小四
    st.font.name = BODY_LATIN
    st.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_EA)
    st.paragraph_format.line_spacing = 1.5
    st.paragraph_format.space_after = Pt(0)

    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.5)
    sec.top_margin = sec.bottom_margin = Cm(2.54)

    i, n_img, n_tab = 0, 0, 0
    while i < len(lines):
        line = lines[i].rstrip()
        s = line.strip()
        if not s:
            i += 1
            continue

        if line.startswith("# ") and not line.startswith("## "):          # 文档标题
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(14)
            add_rich(p, s[2:].strip(), 16, ea=HEAD_EA, latin=HEAD_LATIN, bold=True)
        elif line.startswith("## "):                                       # 一级节标题
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(14)
            p.paragraph_format.space_after = Pt(8)
            add_rich(p, s[3:].strip(), 14, ea=HEAD_EA, latin=HEAD_LATIN, bold=True)
        elif line.startswith("### "):                                      # 二级小节标题
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(10)
            p.paragraph_format.space_after = Pt(6)
            add_rich(p, s[4:].strip(), 12, ea=HEAD_EA, latin=HEAD_LATIN, bold=True)
        elif s.startswith(">"):                                            # 引用块
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.75)
            p.paragraph_format.line_spacing = 1.3
            add_rich(p, s.lstrip("> ").strip(), 10.5, color=GRAY)
        elif IMG_RE.match(s):                                              # 插图
            add_image(doc, (md_path.parent / IMG_RE.match(s).group(1)).resolve())
            n_img += 1
        elif FIG_CAP_RE.match(s):                                          # 图题
            add_caption(doc, s)
        elif TAB_CAP_RE.match(s):                                          # 表题（后面应紧跟表格）
            add_caption(doc, s)
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j + 1 < len(lines) and lines[j].strip().startswith("|") and set(lines[j + 1].strip()) <= set("|-: "):
                k = j + 2
                while k < len(lines) and lines[k].strip().startswith("|"):
                    k += 1
                add_table(doc, [lines[m] for m in range(j, k)])
                n_tab += 1
                i = k - 1
            else:
                print(f"[docx] 警告: 表题后未找到表格: {s[:40]}", file=sys.stderr)
        elif s.startswith("|"):                                            # 无表题的表格
            if i + 1 < len(lines) and set(lines[i + 1].strip()) <= set("|-: ") and lines[i + 1].strip():
                k = i + 2
                while k < len(lines) and lines[k].strip().startswith("|"):
                    k += 1
                add_table(doc, [lines[m] for m in range(i, k)])
                n_tab += 1
                i = k - 1
                print(f"[docx] 警告: 表格缺少表题: {lines[i].strip()[:40]}", file=sys.stderr)
        elif re.match(r"^(\d+[.、)]|[-*•])\s", s):                         # 列表条目
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.85)
            p.paragraph_format.first_line_indent = Cm(-0.85)
            p.paragraph_format.line_spacing = 1.4
            p.paragraph_format.space_after = Pt(4)
            add_rich(p, s, 12)
        else:                                                              # 正文段落
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            indent_2chars(p, 12)
            p.paragraph_format.space_after = Pt(2)
            add_rich(p, s.lstrip("　 "), 12)
        i += 1

    out = out or md_path.with_suffix(".docx")
    doc.save(out)
    print(f"[docx] 完成: {out}（插图 {n_img} 张，表格 {n_tab} 个）")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("md")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    md = Path(args.md)
    if not md.exists():
        die(f"找不到 md: {md}")
    build(md, Path(args.out) if args.out else None)


if __name__ == "__main__":
    main()
