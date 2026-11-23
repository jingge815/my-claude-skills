"""把科研项目申请书（立项书/项目建议书）.md 转换为排版规范的中文 Word 文档。

用法: .venv/bin/python scripts/build_docx.py <申请书.md> [--out 输出.docx] [--template 模板.docx]

支持的 md 子集（与 grant-writing SKILL.md 约定的写作格式一一对应）:
  # 标题              文档标题，黑体三号 16pt 居中加粗
  ## 一、xxx          一级标题，黑体四号 14pt 加粗
  ### （一）xxx       二级标题，黑体小四 12pt 加粗
  #### 1．xxx         三级标题，宋体 12pt 加粗
  ##### 1.1 xxx       四级标题，宋体 12pt 加粗（段前距更小）
  > 说明              灰色说明段（如文首版本行），五号 10.5pt、左缩进
  　　正文段落        剥离段首全角空格，首行缩进 2 字符，宋体/Times New Roman 小四 12pt，
                      1.5 倍行距、两端对齐
  ![](figs/x.png)     居中插图：图片段落强制自动行距（lineRule="auto" 且无 w:line，否则 Word 只显示
                      一个行高、图会「消失」）；宽度 ≤ 版心宽（默认 16.0cm，--template 时取模板节
                      pgSz − pgMar），高度 ≤ 22cm，窄于 700px 的图不放大到 96dpi 原尺寸以上
  图N 题目            居中图题（图片下一行），五号加粗
  表N 题目            居中表题（表格上一行），五号加粗
  | a | b |           GFM 表格：Table Grid，表头加粗浅灰底 EDEDED，单元格 10.5pt，超过 6 列自动调整列宽
  ```…```             代码块：Consolas/宋体 9pt 左缩进逐字渲染，并打印警告（终稿禁止 ASCII 图）
  ## 参考文献         参考文献节：其后 [n] 开头的行按 10.5pt、悬挂缩进 0.75cm、1.3 倍行距渲染
  **加粗** / *斜体* / `代码`   行内加粗转为真实加粗；斜体与反引号剥离标记后保留文字

空行分隔块；其余未识别的行按正文段落渲染并打印警告，不中断构建。
--template 给出时以模板 docx 为底稿：页面设置、页眉页脚、样式沿用模板，原有段落原位保留，新内容追加其后。
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
CODE_LATIN = "Consolas"
BODY_PT = 12               # 小四
CAPTION_PT = 10.5          # 五号（图题、表题、说明段、参考文献、表格单元格）
CODE_PT = 9
DEFAULT_TEXT_WIDTH_CM = 16.0     # A4 21cm − 左右各 2.5cm
MAX_IMG_H_CM = 22.0
EMU_PER_CM = 360000
HEADER_FILL = "EDEDED"
GRAY = RGBColor(0x59, 0x59, 0x59)

# (字号, 中文字体, 西文字体, 段前 pt, 段后 pt)，下标 = 标题级别 − 2（## → 0）
HEADING_FMT = (
    (14, HEAD_EA, HEAD_LATIN, 14, 8),
    (12, HEAD_EA, HEAD_LATIN, 10, 6),
    (12, BODY_EA, BODY_LATIN, 8, 4),
    (12, BODY_EA, BODY_LATIN, 4, 2),
)

# 行内标记：**加粗** | *斜体*（剥离） | `代码`（剥离）。斜体两侧须紧贴非空白且前一个字符不是 ASCII 字母数字，
# 避免 3*4*5 这类乘法被误剥。
INLINE_RE = re.compile(
    r"\*\*(.+?)\*\*|(?<![A-Za-z0-9*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)|`([^`\n]*)`")
IMG_RE = re.compile(r"^!\[[^\]]*\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)$")
URL_RE = re.compile(r"^[A-Za-z][\w+.-]*://")
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
REF_HEAD_RE = re.compile(r"^(?:[一二三四五六七八九十]+、|\d+[、.．])?参考文献$")
REF_RE = re.compile(r"^\[\d+\]\s*\S")
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
CAP_RE = re.compile(r"^([图表])\s*(\d+)\s*(\S.{0,80})$")
HR_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
LIST_RE = re.compile(r"^(?:\d+[.、)]|[-*•])\s+\S")
HTML_RE = re.compile(r"</?[a-zA-Z][^>]*>")

# w:pPr 子元素的 schema 顺序（w:pStyle 之后依次），手工插入元素时据此定位。
PPR_ORDER = (
    "w:pStyle", "w:keepNext", "w:keepLines", "w:pageBreakBefore", "w:framePr", "w:widowControl",
    "w:numPr", "w:suppressLineNumbers", "w:pBdr", "w:shd", "w:tabs", "w:suppressAutoHyphens",
    "w:kinsoku", "w:wordWrap", "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN",
    "w:bidi", "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing",
    "w:mirrorIndents", "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
    "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
)
TBLPR_ORDER = (
    "w:tblStyle", "w:tblpPr", "w:tblOverlap", "w:bidiVisual", "w:tblStyleRowBandSize",
    "w:tblStyleColBandSize", "w:tblW", "w:jc", "w:tblCellSpacing", "w:tblInd", "w:tblBorders", "w:shd",
    "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription",
)


def die(msg: str):
    print(f"[docx] 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg: str):
    print(f"[docx] 警告: {msg}", file=sys.stderr)


def _insert_ordered(parent, el, order):
    """按 schema 顺序插入：放到第一个「排在它之后」的既有子元素前面，没有则追加。"""
    rank = {qn(t): i for i, t in enumerate(order)}
    mine = rank.get(el.tag, len(order))
    for child in parent:
        if rank.get(child.tag, len(order)) > mine:
            child.addprevious(el)
            return el
    parent.append(el)
    return el


def ppr_insert(ppr, el):
    """把 el 按 OOXML schema 顺序插入 pPr（keepNext…spacing, ind…jc…outlineLvl…rPr）。"""
    return _insert_ordered(ppr, el, PPR_ORDER)


def ppr_get_or_add(ppr, tag):
    el = ppr.find(qn(tag))
    return el if el is not None else ppr_insert(ppr, OxmlElement(tag))


def set_spacing(p, **attrs):
    """直接写段落 w:spacing 的属性（line/lineRule/before/after…），值为 None 表示删除该属性。"""
    sp = ppr_get_or_add(p._p.get_or_add_pPr(), "w:spacing")
    for k, v in attrs.items():
        if v is None:
            sp.attrib.pop(qn(f"w:{k}"), None)
        else:
            sp.set(qn(f"w:{k}"), str(v))


def set_outline_level(p, level):
    ppr_get_or_add(p._p.get_or_add_pPr(), "w:outlineLvl").set(qn("w:val"), str(level))


def no_snap_to_grid(p):
    """关闭对齐网格，避免文档网格把等宽代码/ASCII 图的行高拉歪。"""
    ppr_get_or_add(p._p.get_or_add_pPr(), "w:snapToGrid").set(qn("w:val"), "0")


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
    """渲染行内标记：**加粗** → 加粗 run；*斜体*、`代码` → 剥离标记保留文字。"""
    def emit(t, is_bold):
        if t:
            set_font(p.add_run(t), size, ea=ea, latin=latin, bold=is_bold, color=color)
    pos = 0
    for m in INLINE_RE.finditer(text):
        emit(text[pos:m.start()], bold)
        if m.group(1) is not None:
            emit(m.group(1), True)
        else:
            emit(m.group(2) if m.group(2) is not None else m.group(3), bold)
        pos = m.end()
    emit(text[pos:], bold)


def indent_2chars(p, size_pt):
    """首行缩进 2 字符：firstLineChars 随字号自适应，firstLine 磅值兜底；按 schema 顺序插入。"""
    ppr = p._p.get_or_add_pPr()
    ind = ppr.find(qn("w:ind"))
    if ind is None:
        ind = ppr_insert(ppr, OxmlElement("w:ind"))
    ind.set(qn("w:firstLineChars"), "200")
    ind.set(qn("w:firstLine"), str(int(size_pt * 20 * 2)))


def shade(cell, fill):
    tcpr = cell._element.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcpr.append(shd)


def _tbl_borders():
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "auto")
        borders.append(el)
    return borders


def add_caption(doc, text, *, above):
    """图题（above=False，图下方）与表题（above=True，表上方），居中五号加粗。"""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf = p.paragraph_format
    pf.space_before, pf.space_after = Pt(10 if above else 4), Pt(4 if above else 10)
    pf.line_spacing = 1.2
    if above:
        pf.keep_with_next = True
    add_rich(p, text, CAPTION_PT, bold=True)
    return p


def add_image(doc, path: Path, text_width_emu: int):
    """居中插图，按版心宽等比缩放；段落强制自动行距，否则固定值行距会让 Word 只显示一个行高。"""
    if not path.is_file():
        die(f"图片不存在: {path}")
    try:
        with Image.open(path) as im:
            w_px, h_px = im.size
    except Exception as e:
        die(f"无法读取图片 {path}: {e}")
    w_cm = text_width_emu / EMU_PER_CM
    h_cm = w_cm * h_px / w_px
    if h_cm > MAX_IMG_H_CM:                 # 过长的图按高度上限缩放
        h_cm = MAX_IMG_H_CM
        w_cm = h_cm * w_px / h_px
    if w_px < 700:                          # 分辨率小的图不放大：按 96dpi 原尺寸（仍不超版心）
        w_cm = min(w_cm, w_px / 96 * 2.54)
        h_cm = w_cm * h_px / w_px
    p = doc.add_paragraph()                 # 经 doc.add_paragraph 入树，parent 非空，再 add_picture
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf = p.paragraph_format
    pf.space_before, pf.space_after = Pt(6), Pt(2)
    pf.keep_with_next = True                # 与下方图题同页
    set_spacing(p, line=None, lineRule="auto")
    p.add_run().add_picture(str(path), width=Cm(w_cm), height=Cm(h_cm))
    return p


def split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_separator(s):
    return bool(s) and set(s) <= set("|-: ") and "-" in s


def _cell_para_format(para):
    pf = para.paragraph_format
    pf.space_before, pf.space_after = Pt(2), Pt(2)
    pf.line_spacing = 1.15


def add_table(doc, rows):
    header = split_row(rows[0])
    body = [split_row(r) for r in rows[2:]]
    ncol = len(header)
    t = doc.add_table(rows=1, cols=ncol)
    try:
        t.style = "Table Grid"
    except KeyError:
        _insert_ordered(t._tbl.tblPr, _tbl_borders(), TBLPR_ORDER)
        warn("底稿缺少 Table Grid 样式，表格改用手写框线")
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = ncol > 6                    # 列多时交给 Word 自动调整列宽
    t.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))    # 跨页重复表头
    for i, h in enumerate(header):
        cell = t.rows[0].cells[i]
        para = cell.paragraphs[0]
        _cell_para_format(para)
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        add_rich(para, h, CAPTION_PT, bold=True)
        shade(cell, HEADER_FILL)
    for row in body:
        cells = t.add_row().cells
        for i in range(ncol):
            para = cells[i].paragraphs[0]
            _cell_para_format(para)
            add_rich(para, row[i] if i < len(row) else "", CAPTION_PT)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)   # 表后留白，也避免相邻表格粘连
    return t


def _setup_document(doc):
    st = doc.styles["Normal"]
    st.font.size = Pt(BODY_PT)
    st.font.name = BODY_LATIN
    st.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), BODY_EA)
    st.paragraph_format.line_spacing = 1.5
    st.paragraph_format.space_after = Pt(0)
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.5)
    sec.top_margin = sec.bottom_margin = Cm(2.54)


def _text_width(doc, template):
    """版心宽（EMU）：默认 A4 页边距；模板模式取末节 pgSz − pgMar（新内容追加在末节）。"""
    if template is None:
        return int(DEFAULT_TEXT_WIDTH_CM * EMU_PER_CM), "A4 默认页边距"
    secs = doc.sections
    if not secs:
        warn("模板没有节属性，版心宽度按 A4 默认值")
        return int(DEFAULT_TEXT_WIDTH_CM * EMU_PER_CM), "A4 默认页边距"
    sec = secs[-1]
    if sec.page_width is None or sec.left_margin is None or sec.right_margin is None:
        warn("模板缺少页面设置，缺失项按 A4 默认值补齐")
        if sec.page_width is None:
            sec.page_width, sec.page_height = Cm(21), Cm(29.7)
        if sec.left_margin is None:
            sec.left_margin = Cm(2.5)
        if sec.right_margin is None:
            sec.right_margin = Cm(2.5)
    return sec.page_width - sec.left_margin - sec.right_margin, "模板末节 pgSz − pgMar"


def build(md_path: Path, out: Path, template: Path | None):
    lines = md_path.read_text(encoding="utf-8-sig").splitlines()
    if template is not None:
        try:
            doc = Document(str(template))
        except Exception as e:
            die(f"无法加载模板 {template}: {e}")
        print(f"[docx] 模板模式: 以 {template} 为底稿，原有 {len(doc.paragraphs)} 个段落保留原位，新内容追加其后")
    else:
        doc = Document()
        _setup_document(doc)
    text_width_emu, width_src = _text_width(doc, template)
    print(f"[docx] 版心宽度: {text_width_emu / EMU_PER_CM:.2f} cm（{width_src}）")

    n_title = n_par = n_img = n_tab = n_ref = 0
    in_code, in_refs = False, False
    code_warned = False                                 # 每个代码块只警告一次
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        s = line.strip()
        if FENCE_RE.match(line):                        # 代码块围栏
            if in_code and doc.paragraphs:
                doc.paragraphs[-1].paragraph_format.keep_with_next = False
            in_code = not in_code
            code_warned = False
            i += 1
            continue
        if in_code:                                     # 代码块内逐字渲染（空行也保留）
            p = doc.add_paragraph()
            pf = p.paragraph_format
            pf.left_indent = Cm(0.75)
            pf.space_before = pf.space_after = Pt(0)
            pf.line_spacing = 1.0
            pf.keep_together = pf.keep_with_next = True
            no_snap_to_grid(p)
            if line:
                set_font(p.add_run(line), CODE_PT, latin=CODE_LATIN)
                n_par += 1
            if not code_warned:
                warn(f"正文含代码块（第 {len(doc.paragraphs)} 段），若为 ASCII 图请改为插图")
                code_warned = True
            i += 1
            continue
        if not s:
            i += 1
            continue

        m = HEAD_RE.match(s)
        img_m = IMG_RE.match(s)
        cap_m = CAP_RE.match(s)
        is_caption = bool(cap_m) and not line.startswith((" ", "\t", "　")) \
            and not s.endswith(("。", "；", "，"))
        n_th = len(doc.paragraphs) + 1                  # 即将添加的段落序号（用于警告定位）

        if m:                                           # 标题
            level = len(m.group(1))
            text = re.sub(r"\s+#+$", "", m.group(2)).strip()
            if level == 1:                              # 文档标题
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                pf = p.paragraph_format
                pf.space_before, pf.space_after = Pt(0), Pt(14)
                pf.line_spacing = 1.5
                pf.keep_with_next = True
                add_rich(p, text, 16, ea=HEAD_EA, latin=HEAD_LATIN, bold=True)
                n_title += 1
                if n_title > 1:
                    warn(f"出现第 {n_title} 个「#」标题，均按文档标题渲染（第 {n_th} 段）: {text[:30]}")
            else:                                       # 二~五级 → 标题级别 1~4
                if level > 5:
                    warn(f"标题层级过深（{'#' * level}），按四级标题渲染（第 {n_th} 段）: {text[:30]}")
                size, ea, latin, before, after = HEADING_FMT[min(level - 2, 3)]
                p = doc.add_paragraph()
                pf = p.paragraph_format
                pf.space_before, pf.space_after = Pt(before), Pt(after)
                pf.line_spacing = 1.5
                pf.keep_with_next = True
                set_outline_level(p, min(level - 2, 3))
                add_rich(p, text, size, ea=ea, latin=latin, bold=True)
                n_par += 1
                if level <= 3:
                    in_refs = bool(REF_HEAD_RE.match(text))
        elif s.startswith(">"):                         # 说明段（版本行等）
            p = doc.add_paragraph()
            pf = p.paragraph_format
            pf.left_indent = Cm(0.75)
            pf.space_before = pf.space_after = Pt(2)
            pf.line_spacing = 1.3
            add_rich(p, s[1:].strip(), CAPTION_PT, color=GRAY)
            n_par += 1
        elif img_m:                                     # 插图
            src = img_m.group(1)
            if URL_RE.match(src):
                die(f"不支持 URL 图片，请先下载到本地再引用: {src}")
            add_image(doc, (md_path.parent / src).resolve(), text_width_emu)
            n_img += 1
        elif is_caption and cap_m.group(1) == "图":     # 图题
            add_caption(doc, s, above=False)
            n_par += 1
        elif is_caption:                                # 表题（后面应紧跟表格）
            add_caption(doc, s, above=True)
            n_par += 1
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j + 1 < len(lines) and lines[j].strip().startswith("|") and is_separator(lines[j + 1].strip()):
                k = j + 2
                while k < len(lines) and lines[k].strip().startswith("|"):
                    k += 1
                add_table(doc, lines[j:k])
                n_tab += 1
                i = k
                continue
            warn(f"表题后未找到表格（第 {n_th} 段）: {s[:40]}")
        elif s.startswith("|"):                         # 无表题的表格
            if i + 1 < len(lines) and is_separator(lines[i + 1].strip()):
                k = i + 2
                while k < len(lines) and lines[k].strip().startswith("|"):
                    k += 1
                add_table(doc, lines[i:k])
                n_tab += 1
                warn(f"表格缺少表题（第 {n_th} 段前）: {s[:40]}")
                i = k
                continue
            warn(f"表格缺少分隔行，按正文渲染（第 {n_th} 段）: {s[:40]}")
            n_par += _add_body(doc, s)
        elif HR_RE.match(s):
            warn(f"分隔线已忽略（第 {n_th} 段）")
        elif REF_RE.match(s):                           # 参考文献条目
            if not in_refs:
                warn(f"参考文献条目出现在「参考文献」标题之前（第 {n_th} 段）: {s[:30]}")
            p = doc.add_paragraph()
            pf = p.paragraph_format
            pf.left_indent = Cm(0.75)
            pf.first_line_indent = Cm(-0.75)
            pf.space_after = Pt(2)
            pf.line_spacing = 1.3
            add_rich(p, s, CAPTION_PT)
            n_par += 1
            n_ref += 1
        else:                                           # 正文段落（含未知构造）
            if LIST_RE.match(s):
                warn(f"正文含列表条目，申请书正文应为连贯段落（第 {n_th} 段）: {s[:30]}")
            elif HTML_RE.search(s):
                warn(f"正文含未支持的 HTML 标签，按原样输出（第 {n_th} 段）: {s[:30]}")
            n_par += _add_body(doc, s)
        i += 1

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    print(f"[docx] 完成: {out}（标题 {n_title}，段落 {n_par}，插图 {n_img} 张，表格 {n_tab} 个，参考文献 {n_ref} 条）")


def _add_body(doc, s):
    """正文段落：宋体/Times New Roman 小四，1.5 倍行距，首行缩进 2 字符，两端对齐。"""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    pf = p.paragraph_format
    pf.line_spacing = 1.5
    pf.space_after = Pt(2)
    indent_2chars(p, BODY_PT)
    add_rich(p, s, BODY_PT)
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("md", help="申请书 markdown 源文件")
    ap.add_argument("--out", default=None, help="输出 docx（默认与 md 同名同目录）")
    ap.add_argument("--template", default=None,
                    help="以该 docx 为底稿：沿用页面设置/页眉页脚/样式，原有段落保留原位，新内容追加其后")
    args = ap.parse_args()
    md = Path(args.md)
    if not md.is_file():
        die(f"找不到 md: {md}")
    template = Path(args.template) if args.template else None
    if template is not None and not template.is_file():
        die(f"找不到模板: {template}")
    build(md, Path(args.out) if args.out else md.with_suffix(".docx"), template)


if __name__ == "__main__":
    main()
