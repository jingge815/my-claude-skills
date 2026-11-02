"""把 layout 引擎输出的元素写成可编辑的 pptx，旁白写进每页备注。

用法: pr.sh build_pptx <content.json> [--out 输出.pptx]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt
from lxml import etree

from common import (PPT_FONT_EA, PPT_FONT_LATIN, SLIDE_H, SLIDE_W, THEME, die, load_content,
                    load_figures, log, parse_rich, plain, slug_of)
from layout import BULLET_CHAR_SCALE, SlideError, layout_slide, overflow_report

ALIGN = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}
ANCHOR = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE, "b": MSO_ANCHOR.BOTTOM}


def rgb(h):
    return RGBColor.from_string(h.lstrip("#"))


def set_run_font(run, size, color, bold, italic=False):
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.italic = italic
    f.color.rgb = rgb(color)
    f.name = PPT_FONT_LATIN
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = etree.SubElement(rpr, qn(tag))
        el.set("typeface", PPT_FONT_EA)


def set_bullet(p, char, indent_in, color, scale):
    ppr = p._p.get_or_add_pPr()
    ppr.set("marL", str(Emu(Inches(indent_in))))
    ppr.set("indent", str(-Emu(Inches(0.28))))
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum", "a:buClr", "a:buSzPct", "a:buFont"):
        for el in ppr.findall(qn(tag)):
            ppr.remove(el)
    clr = etree.SubElement(ppr, qn("a:buClr"))
    etree.SubElement(clr, qn("a:srgbClr")).set("val", color)
    etree.SubElement(ppr, qn("a:buSzPct")).set("val", str(int(scale * 100000)))
    etree.SubElement(ppr, qn("a:buFont")).set("typeface", "Arial")
    etree.SubElement(ppr, qn("a:buChar")).set("char", char)


def set_spacing(p, space_before_pt, line_pct=115):
    ppr = p._p.get_or_add_pPr()
    for tag in ("a:lnSpc", "a:spcBef"):
        for el in ppr.findall(qn(tag)):
            ppr.remove(el)
    ln = etree.Element(qn("a:lnSpc"))
    etree.SubElement(ln, qn("a:spcPct")).set("val", str(line_pct * 1000))
    sb = etree.Element(qn("a:spcBef"))
    etree.SubElement(sb, qn("a:spcPts")).set("val", str(int(space_before_pt * 100)))
    # schema 顺序：lnSpc, spcBef, ... 必须在 buClr 等之前
    ppr.insert(0, sb)
    ppr.insert(0, ln)


def fill_paragraphs(tf, fitted, line_pct=115):
    tf.clear()
    for i, fp in enumerate(fitted):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = ALIGN[fp["align"]]
        runs = parse_rich(fp["text"]) or [("", False)]
        for text, emph in runs:
            r = p.add_run()
            r.text = text
            bold = fp["bold"] or emph
            color = THEME["accent"] if emph and fp["color"] == THEME["text"] else fp["color"]
            set_run_font(r, fp["size"], color, bold, fp.get("italic", False))
        set_spacing(p, fp["space_before_in"] * 72.0, line_pct)
        if fp["bullet"]:
            bcolor = THEME["accent"] if fp["level"] == 0 else THEME["muted"]
            if fp["color"] == THEME["white"]:
                bcolor = THEME["highlight"]
            set_bullet(p, fp["bullet"], fp["indent"], bcolor, BULLET_CHAR_SCALE[fp["level"]])


def add_text(slide, e):
    tb = slide.shapes.add_textbox(Inches(e["x"]), Inches(e["y"]), Inches(e["w"]), Inches(e["h"]))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, side, 0)
    tf.vertical_anchor = ANCHOR[e["valign"]]
    fill_paragraphs(tf, e["fitted"])


def add_rect(slide, e):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(e["x"]), Inches(e["y"]),
                                   Inches(e["w"]), Inches(e["h"]))
    if e["fill"]:
        shape.fill.solid()
        shape.fill.fore_color.rgb = rgb(e["fill"])
    else:
        shape.fill.background()
    if e["line"]:
        shape.line.color.rgb = rgb(e["line"])
        shape.line.width = Pt(e["line_w"])
    else:
        shape.line.fill.background()
    shape.shadow.inherit = False
    shape.text_frame.text = ""


def add_table(slide, e):
    nrows = len(e["rows"]) + 1
    ncols = len(e["header"])
    gs = slide.shapes.add_table(nrows, ncols, Inches(e["x"]), Inches(e["y"]),
                                Inches(e["w"]), Inches(e["h"]))
    tbl = gs.table
    # 去掉内置表格样式的条纹，改为手动着色
    tbl.first_row = True
    tbl.horz_banding = False
    for ci, w in enumerate(e["col_w"]):
        tbl.columns[ci].width = Inches(w)
    for ri, h in enumerate(e["row_h"]):
        tbl.rows[ri].height = Inches(h)
    all_rows = [e["header"]] + e["rows"]
    for ri, row in enumerate(all_rows):
        for ci, val in enumerate(row):
            cell = tbl.cell(ri, ci)
            cell.margin_left = cell.margin_right = Inches(e["pad_x"])
            cell.margin_top = cell.margin_bottom = Inches(e["pad_y"])
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            if ri == 0:
                cell.fill.fore_color.rgb = rgb(THEME["primary"])
            else:
                cell.fill.fore_color.rgb = rgb(THEME["soft"] if ri % 2 == 0 else THEME["white"])
            tf = cell.text_frame
            tf.word_wrap = True
            fp = {"text": val, "size": e["size"], "color": THEME["white"] if ri == 0 else THEME["text"],
                  "bold": ri == 0, "align": "c" if ri == 0 or ci > 0 else "l", "bullet": None,
                  "level": 0, "space_before_in": 0, "indent": 0}
            fill_paragraphs(tf, [fp], line_pct=100)


def build(content_path: Path, out: Path | None = None, strict: bool = True) -> Path:
    content = load_content(content_path)
    meta = content.get("meta", {})
    figs = load_figures(content_path, content)
    slides = content.get("slides") or die("content.json 里没有 slides")

    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W)
    prs.slide_height = Inches(SLIDE_H)
    blank = prs.slide_layouts[6]
    problems = []
    for i, s in enumerate(slides, 1):
        try:
            els = layout_slide(s, meta, figs, content_path, i, len(slides))
        except SlideError as ex:
            die(f"第 {i} 页（{s.get('title', s.get('type'))}）：{ex}")
        for m in overflow_report(els):
            problems.append(f"第 {i} 页：{m}")
        slide = prs.slides.add_slide(blank)
        for e in els:
            k = e["kind"]
            if k == "rect":
                add_rect(slide, e)
            elif k == "text":
                add_text(slide, e)
            elif k == "image":
                slide.shapes.add_picture(e["file"], Inches(e["x"]), Inches(e["y"]),
                                         Inches(e["w"]), Inches(e["h"]))
            elif k == "table":
                add_table(slide, e)
        note = plain(s.get("narration", "")).strip()
        if note:
            slide.notes_slide.notes_text_frame.text = note
    for p in problems:
        log("警告：" + p)
    out = out or (Path(content_path).resolve().parent / "output" / f"{slug_of(content)}_slides.pptx")
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    log(f"PPT 已生成：{out}（{len(slides)} 页）")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("content")
    ap.add_argument("--out")
    a = ap.parse_args()
    build(Path(a.content), Path(a.out) if a.out else None)


if __name__ == "__main__":
    main()
