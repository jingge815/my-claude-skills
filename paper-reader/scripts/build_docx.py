"""根据 content.json 的 summary 部分生成 Word 论文总结。

用法: pr.sh build_docx <content.json> [--out 输出.docx]

summary 结构见 references/content-schema.md。块类型：
  {"type":"p","text":"..."}                     段落，支持 **强调**
  {"type":"bullets","items":["...", ...]}         无序列表
  {"type":"numbered","items":["...", ...]}        有序列表
  {"type":"figure","ref":"fig3","caption":"..."}  插图（figures.json 的 id 或相对路径）
  {"type":"table","header":[...],"rows":[[...]],"caption":"..."}
  {"type":"quote","text":"..."}                   原文引用（英文原句）
"""
from __future__ import annotations

import argparse
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT  # noqa: F401
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from common import THEME, die, load_content, load_figures, log, parse_rich, resolve_figure, slug_of

FONT_EA = "微软雅黑"
FONT_LATIN = "Arial"
BODY_FONT_EA = "宋体"
BODY_FONT_LATIN = "Times New Roman"


def set_font(run, size=None, bold=None, color=None, ea=BODY_FONT_EA, latin=BODY_FONT_LATIN, italic=None):
    f = run.font
    if size:
        f.size = Pt(size)
    if bold is not None:
        f.bold = bold
    if italic is not None:
        f.italic = italic
    if color:
        f.color.rgb = RGBColor.from_string(color)
    f.name = latin
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for k in ("w:ascii", "w:hAnsi", "w:cs"):
        rfonts.set(qn(k), latin)
    rfonts.set(qn("w:eastAsia"), ea)


def style_doc(doc):
    st = doc.styles["Normal"]
    st.font.size = Pt(11)
    st.font.name = BODY_FONT_LATIN
    st.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT_EA)
    st.paragraph_format.line_spacing = 1.4
    st.paragraph_format.space_after = Pt(4)
    for name, size, color in (("Title", 20, THEME["primary"]), ("Heading 1", 15, THEME["primary"]),
                              ("Heading 2", 13, THEME["accent"]), ("Heading 3", 12, THEME["text"])):
        s = doc.styles[name]
        s.font.size = Pt(size)
        s.font.bold = True
        s.font.color.rgb = RGBColor.from_string(color)
        s.font.name = FONT_LATIN
        rpr = s.element.get_or_add_rPr()
        rf = rpr.find(qn("w:rFonts"))
        if rf is None:
            rf = OxmlElement("w:rFonts")
            rpr.insert(0, rf)
        for k in ("w:ascii", "w:hAnsi", "w:cs"):
            rf.set(qn(k), FONT_LATIN)
        rf.set(qn("w:eastAsia"), FONT_EA)
        rf.attrib.pop(qn("w:asciiTheme"), None)
        rf.attrib.pop(qn("w:eastAsiaTheme"), None)
        rf.attrib.pop(qn("w:hAnsiTheme"), None)
        s.paragraph_format.space_before = Pt(14 if name == "Heading 1" else 10)
        s.paragraph_format.space_after = Pt(6)
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.5)
    sec.top_margin = sec.bottom_margin = Cm(2.3)


def add_rich(p, text, size=None, color=None, bold=False, italic=None):
    for t, emph in parse_rich(text):
        r = p.add_run(t)
        set_font(r, size=size, bold=bold or emph, color=(THEME["accent"] if emph and not color else color),
                 italic=italic)


def shade(cell, hex_fill):
    tcpr = cell._element.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcpr.append(shd)


def caption(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_rich(p, text, size=9.5, color=THEME["muted"])
    p.paragraph_format.space_after = Pt(10)


def add_table(doc, header, rows, cap=None, widths=None):
    if cap:
        caption(doc, cap)
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(header):
        c = t.rows[0].cells[i]
        c.text = ""
        add_rich(c.paragraphs[0], str(h), size=10, bold=True, color=THEME["white"])
        shade(c, THEME["primary"])
    for ri, r in enumerate(rows):
        if len(r) != len(header):
            die(f"Word 表格列数不一致：{r}")
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = ""
            add_rich(cells[i].paragraphs[0], str(v), size=10)
            if ri % 2 == 1:
                shade(cells[i], THEME["soft"])
    if widths:
        tot = sum(widths)
        for row in t.rows:
            for i, c in enumerate(row.cells):
                c.width = Cm(16 * widths[i] / tot)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def add_block(doc, b, figs, cpath):
    t = b.get("type", "p")
    if t == "p":
        add_rich(doc.add_paragraph(), b["text"])
    elif t in ("bullets", "numbered"):
        style = "List Bullet" if t == "bullets" else "List Number"
        for it in b["items"]:
            add_rich(doc.add_paragraph(style=style), it)
    elif t == "quote":
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(0.8)
        add_rich(p, b["text"], size=10, color=THEME["muted"], italic=True)
    elif t == "figure":
        fig = resolve_figure(b.get("ref"), figs, cpath)
        if fig is None:
            die(f"Word 插图找不到引用 {b.get('ref')!r}")
        width = b.get("width_cm") or (15.5 if fig.get("aspect", 1.5) > 1.6 else 11)
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(fig["file"], width=Cm(width))
        caption(doc, b.get("caption") or fig.get("label", ""))
    elif t == "table":
        add_table(doc, b["header"], b["rows"], b.get("caption"), b.get("col_widths"))
    else:
        die(f"Word 未知块类型 {t!r}")


def build(content_path: Path, out: Path | None = None) -> Path:
    content = load_content(content_path)
    meta = content.get("meta", {})
    summ = content.get("summary") or die("content.json 缺少 summary")
    figs = load_figures(content_path, content)

    doc = Document()
    style_doc(doc)

    p = doc.add_paragraph(style="Title")
    add_rich(p, summ.get("doc_title") or f"论文精读：{meta.get('title', '')}", size=20, bold=True,
             color=THEME["primary"])
    if meta.get("title_zh"):
        p = doc.add_paragraph()
        add_rich(p, meta["title_zh"], size=13, color=THEME["accent"])

    info = [("论文", meta.get("title", "")), ("作者", meta.get("authors", "")),
            ("机构", meta.get("affiliations", "")), ("发表", " ".join(
                v for v in [meta.get("venue", ""), str(meta.get("year", "") or "")] if v)),
            ("DOI / 链接", meta.get("doi", "") or meta.get("url", "")),
            ("关键词", "、".join(meta.get("keywords", []) if isinstance(meta.get("keywords"), list)
                               else [meta.get("keywords", "")]))]
    info = [(k, v) for k, v in info if v]
    if info:
        t = doc.add_table(rows=0, cols=2)
        t.style = "Table Grid"
        for k, v in info:
            row = t.add_row().cells
            row[0].text = ""
            add_rich(row[0].paragraphs[0], k, size=10, bold=True, color=THEME["primary"])
            shade(row[0], THEME["soft"])
            row[1].text = ""
            add_rich(row[1].paragraphs[0], v, size=10)
            row[0].width, row[1].width = Cm(2.8), Cm(13.2)
        doc.add_paragraph()

    if summ.get("tldr"):
        doc.add_heading("一句话总结", level=1)
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(0.4)
        add_rich(p, summ["tldr"], size=12, bold=True, color=THEME["primary"])

    for sec in summ.get("sections", []):
        doc.add_heading(sec["heading"], level=1)
        for b in sec.get("blocks", []):
            if b.get("type") == "h2":
                doc.add_heading(b["text"], level=2)
            else:
                add_block(doc, b, figs, content_path)

    # 页脚页码
    footer = doc.sections[0].footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run()
    for tag, txt in (("begin", None), (None, "PAGE"), ("end", None)):
        if tag:
            fc = OxmlElement("w:fldChar")
            fc.set(qn("w:fldCharType"), tag)
            run._r.append(fc)
        else:
            it = OxmlElement("w:instrText")
            it.set(qn("xml:space"), "preserve")
            it.text = txt
            run._r.append(it)

    out = out or (Path(content_path).resolve().parent / "output" / f"{slug_of(content)}_summary.docx")
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    log(f"Word 总结已生成：{out}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("content")
    ap.add_argument("--out")
    a = ap.parse_args()
    build(Path(a.content), Path(a.out) if a.out else None)


if __name__ == "__main__":
    main()
