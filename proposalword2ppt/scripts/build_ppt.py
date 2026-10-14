#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""proposalword2ppt 模板填充器。

把 content.json 的逐页内容填进 assets/template.pptx 的固定版式，输出可直接放映、
可继续编辑的答辩 PPT（旁白写入 PowerPoint 备注页）。

文本填进既有槽位；图表槽把模板的「图/表」色块换成按主题色绘制（或申请书原图）
的 PNG。正文字号按文本框几何与字数在 12–18 磅之间自适应，不改母版/背景/配色。

图表绘制需要 Pillow；未安装时图表槽退回文字说明，并在校验里提示。

用法：
    python3 build_ppt.py --content content.json --out 答辩PPT.pptx
    python3 build_ppt.py --content content.json --out out.pptx --narration-out 旁白稿.md
退出码：0 成功；1 参数或内容错误；2 校验发现致命问题（内容未写入全部页面）。
"""

import argparse
import json
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# 图表全部由一个模块负责：自己画的图出原生形状（可编辑），申请书原图按位图嵌入。
try:
    import shapes as _shapes
except ImportError:
    _shapes = None

# --------------------------------------------------------------------------
# 模板槽位定义（名称取自 template.pptx 中各形状的 cNvPr name，改动模板需同步改这里）
# --------------------------------------------------------------------------
BLUEPRINTS = {
    "cover": 1,          # 封面：标题/副标题占位符 + 右下角报告人文本框
    "accumulation": 2,   # 积累页：图表在左、文字在右、底部总结
    "text-left": 3,      # 左文右图（需求分析、研究现状）
    "figure-left": 5,    # 左图右文（研究概述、研究内容、技术路线、指标、成果、周期、经费）
    "closing": 16,       # 结束语：总结 + 感谢语 + ISCAS 标识
}
SLOT_TITLE = "文本框 1"
SLOT_FIGURE = "矩形 2"
SLOT_BODY = "文本框 31"
SLOT_SUMMARY = "文本框 33"
FIG_PLACEHOLDER = "图/表"

REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

# 版式自查阈值（依据 references/template-spec.md 的实测几何推算）
MAX_POINTS = 6
ACCUMULATION_POINTS = 4   # 第 2 页固定四栏：论文 / 项目 / 厂商 / 优化
MAX_POINT_CHARS = 28
# 研究现状要把「现状原理 + 对应不足」讲清楚，一条自然更长，单独放宽
STATUS_POINT_CHARS = 44
MAX_SUMMARY_CHARS = 60
# 研究内容 / 技术路线 / 概述的总结句按模板写（问题+不足+方案 / 方法+对比+优势），
# 本身就比普通判断句长
TEMPLATE_SUMMARY_CHARS = 74
CLOSING_SUMMARY_CHARS = 90  # 结束语收尾句要写足单位优势，允许更长


def point_char_limit(page):
    return STATUS_POINT_CHARS if "研究现状" in (page.get("title") or "") else MAX_POINT_CHARS


def summary_char_limit(page):
    title = page.get("title") or ""
    if page.get("kind") == "closing":
        return CLOSING_SUMMARY_CHARS
    if any(k in title for k in ("研究内容", "研究技术路线", "研究概述")):
        return TEMPLATE_SUMMARY_CHARS
    return MAX_SUMMARY_CHARS
NARRATION_MIN = 1800
NARRATION_MAX = 2000
BODY_PT_DEFAULT = 16
BODY_PT_MIN = 12
BODY_PT_MAX = 18
SUMMARY_PT_DEFAULT = 16
SUMMARY_PT_MIN = 12
SUMMARY_READABLE_PT = 14   # 低于这个磅数的总结句必须改短
SUMMARY_ONE_LINE_CHARS = 52  # 30.55 cm 宽、16 磅时一行约能放这么多字
CM_PER_EMU = 1.0 / 360000.0

# 正文框实测：宽 9.89 cm。
# 正文与图表槽垂直对齐：上沿取两者较高者，下沿收到总结框上方 0.3 cm。
# 模板原文的正文框只有 3.93 cm、且与图表槽上下错开，直接填会显得一边高一边矮。
BODY_WIDTH_CM = 9.89
BODY_INDENT_CM = 0.79375
# 图表槽上沿、下沿（cm），来自模板矩形 2
# 图表槽左沿。text-left 的正文右沿（13.52）压进图表左沿（12.56）约 1 cm，
# 模板原文靠「正文更靠下」躲开了一部分；垂直对齐后必须把正文收窄，否则字压在图上。
FIGURE_LEFT = {
    "text-left": 12.56,
}
BODY_RIGHT_GAP_CM = 0.40
FIGURE_BOTTOM = {
    "accumulation": 13.76,
    "text-left": 13.76,
    "figure-left": 13.76,
}
FIGURE_TOP = {
    "accumulation": 4.74,
    "text-left": 4.74,
    "figure-left": 4.74,
}
# 总结框上沿
SUMMARY_TOP = {
    "accumulation": 15.78,
    "text-left": 17.05,
    "figure-left": 17.05,
}
COVER_META_PT = 20        # 封面「报告人 / 日期」
COVER_META_ORG_PT = 18    # 封面单位行，比姓名略小
# 封面右下角文本框：模板只有 5.94 × 1.80 cm，三行放不下
COVER_META_X = int(round(21.2 / CM_PER_EMU))
COVER_META_Y = int(round(14.75 / CM_PER_EMU))
COVER_META_CX = int(round(11.4 / CM_PER_EMU))
COVER_META_CY = int(round(3.35 / CM_PER_EMU))
# 图在左、字在右时，正文左沿按「图右沿 + 间隙」定，贴回图边。
# 模板把正文放在 23.06，离图 4 cm，中间空一大条。
FIGURE_RIGHT = {
    "accumulation": 19.77,
    "figure-left": 19.02,
}
FIG_GAP_CM = {
    "accumulation": 1.2,
    "figure-left": 2.0,
}

SP_RE = re.compile(r"<p:sp>.*?</p:sp>", re.S)
TXBODY_RE = re.compile(r"<p:txBody>.*?</p:txBody>", re.S)
PARA_RE = re.compile(r"<a:p>.*?</a:p>", re.S)
RUN_RE = re.compile(r"<a:r>.*?</a:r>", re.S)
RPR_RE = re.compile(r"<a:rPr[^>]*/>|<a:rPr[^>]*>.*?</a:rPr>", re.S)
ENDPR_RE = re.compile(r"<a:endParaRPr[^>]*/>|<a:endParaRPr[^>]*>.*?</a:endParaRPr>", re.S)
NAME_RE = re.compile(r'<p:cNvPr[^>]*\bname="([^"]*)"')


def assert_xml_wellformed(xml, where):
    """生成时就校验 XML 良构，别等到 PowerPoint 打不开才发现。"""
    import xml.etree.ElementTree as ET

    try:
        ET.fromstring(xml)
    except ET.ParseError as exc:
        raise SystemExit(
            "%s 生成的 XML 不良构：%s\n（这类问题会让 PPT 打不开，属于脚本 bug，"
            "请修脚本而不是绕过）" % (where, exc)
        )


def strip_decl(xml):
    """去掉模板自带的 XML 声明，便于统一重新拼接。"""
    return re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", xml)


def esc(text):
    """转义 XML 文本。"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# --------------------------------------------------------------------------
# XML 片段手术：形状定位、段落克隆、文本替换
# --------------------------------------------------------------------------
def find_shape(xml, name):
    """按形状名返回 <p:sp>...</p:sp> 片段；找不到返回 None。"""
    for block in SP_RE.findall(xml):
        m = NAME_RE.search(block)
        if m and m.group(1) == name:
            return block
    return None


def shape_paragraphs(block):
    """取形状内所有 <a:p> 片段。"""
    body = TXBODY_RE.search(block)
    if body is None:
        return []
    return PARA_RE.findall(body.group(0))


def set_sz(rpr, sz):
    """给 rPr 片段设置字号（sz 单位为 1/100 磅）。"""
    if rpr is None:
        return None
    if re.search(r'\bsz="\d+"', rpr):
        return re.sub(r'\bsz="\d+"', 'sz="%d"' % sz, rpr)
    return rpr.replace("<a:rPr", '<a:rPr sz="%d"' % sz, 1)


def set_typeface(rpr, ea, latin, cs=None):
    """给 rPr 片段设置中英文字体。

    收尾标签名必须从输入里取——段落末尾的是 <a:endParaRPr>，
    写死 </a:rPr> 会生成不闭合的 XML。
    """
    if rpr is None:
        return None
    m = re.match(r"<a:(\w+)", rpr)
    tag = m.group(1) if m else "rPr"
    cs = cs or latin
    rpr = re.sub(r"<a:(latin|ea|cs)\b[^>]*/>", "", rpr)
    faces = (
        '<a:latin typeface="%s"/><a:ea typeface="%s"/><a:cs typeface="%s"/>'
        % (latin, ea, cs)
    )
    closer = "</a:%s>" % tag
    if rpr.endswith("/>"):  # 自闭合 → 展开成带子元素的形式
        return rpr[:-2].rstrip() + ">" + faces + closer
    # sym / hlinkClick 必须排在 latin/ea/cs 之后
    m = re.search(r"<a:(sym|hlinkClick|hlinkMouseOver)\b", rpr)
    idx = m.start() if m else rpr.rindex(closer)
    return rpr[:idx] + faces + rpr[idx:]


def norm_lang(rpr):
    """统一语言标记，避免中文落在 en-US 备用字体上。"""
    if rpr is None:
        return None
    rpr = re.sub(r'\blang="[^"]*"', 'lang="zh-CN"', rpr)
    rpr = re.sub(r'\baltLang="[^"]*"', 'altLang="en-US"', rpr)
    return rpr


def build_paragraph(para_tpl, runs, sz=None, typeface=None):
    """克隆模板段落，替换其中的文本 run。

    para_tpl: 模板 <a:p> 片段，提供 pPr（项目符号、行距、缩进）与 run/rPr 样式
    runs: [(文本, 模板 run 序号)]，模板 run 序号按段落内 <a:r> 出现顺序取
    """
    rprs = []
    for run in RUN_RE.findall(para_tpl):
        m = RPR_RE.search(run)
        rprs.append(m.group(0) if m else None)
    if not rprs:
        rprs = [None]
    m = ENDPR_RE.search(para_tpl)
    endpr = m.group(0) if m else None

    body = RUN_RE.sub("", para_tpl)
    if endpr:
        body = body.replace(endpr, "")

    out_runs = []
    for text, idx in runs:
        rpr = rprs[min(idx, len(rprs) - 1)]
        if rpr is not None:
            rpr = norm_lang(rpr)
            if sz is not None:
                rpr = set_sz(rpr, sz)
            if typeface is not None:
                rpr = set_typeface(rpr, *typeface)
            out_runs.append("<a:r>%s<a:t>%s</a:t></a:r>" % (rpr, esc(text)))
        else:
            out_runs.append("<a:r><a:t>%s</a:t></a:r>" % esc(text))

    insert = "".join(out_runs)
    if endpr:
        if sz is not None:
            endpr = set_sz(endpr, sz)
        if typeface is not None:
            endpr = set_typeface(endpr, *typeface)
        insert += endpr
    return body.replace("</a:p>", insert + "</a:p>")


def fill_shape(xml, name, specs, sz=None, typeface=None):
    """把 specs 写入指定形状。

    specs: [(模板段落序号, [(文本, 模板 run 序号), ...]), ...]
    """
    block = find_shape(xml, name)
    if block is None:
        raise KeyError("模板中找不到形状：%s" % name)
    paras = shape_paragraphs(block)
    if not paras:
        raise KeyError("形状 %s 没有段落模板" % name)
    new = "".join(
        build_paragraph(paras[min(ptpl, len(paras) - 1)], runs, sz, typeface)
        for ptpl, runs in specs
    )
    body = TXBODY_RE.search(block).group(0)
    open_tag = body[: body.index("<a:p>")] if "<a:p>" in body else body[: -len("</p:txBody>")]
    new_body = open_tag + new + "</p:txBody>"
    return xml.replace(block, block.replace(body, new_body))


def fill_shape_mixed(xml, name, specs, sizes, typeface=None):
    """逐段给不同字号的 fill_shape。sizes 与 specs 一一对应（单位：磅）。"""
    block = find_shape(xml, name)
    if block is None:
        raise KeyError("模板中找不到形状：%s" % name)
    paras = shape_paragraphs(block)
    if not paras:
        raise KeyError("形状 %s 没有段落模板" % name)
    new = "".join(
        build_paragraph(paras[min(ptpl, len(paras) - 1)], runs, sz * 100, typeface)
        for (ptpl, runs), sz in zip(specs, sizes)
    )
    body = TXBODY_RE.search(block).group(0)
    open_tag = body[: body.index("<a:p>")] if "<a:p>" in body else body[: -len("</p:txBody>")]
    new_body = open_tag + new + "</p:txBody>"
    return xml.replace(block, block.replace(body, new_body))


def force_align(xml, name, algn="ctr"):
    """强制段落对齐方式，不依赖版式继承。"""
    block = find_shape(xml, name)
    if block is None:
        return xml
    body = TXBODY_RE.search(block)
    if body is None:
        return xml
    new_body = re.sub(r"<a:pPr\b(?![^>]*\balgn=)", '<a:pPr algn="%s"' % algn, body.group(0))
    new_body = re.sub(r'<a:pPr\b([^>]*?)\balgn="[^"]*"', r'<a:pPr\1algn="%s"' % algn, new_body)
    if "<a:pPr" not in new_body:
        new_body = new_body.replace("<a:p>", '<a:p><a:pPr algn="%s"/>' % algn)
    return xml.replace(block, block.replace(body.group(0), new_body))


def clear_shape(xml, name):
    """清空形状文本，只留一个空段落。

    必须调用：模板里的槽位带「xxx」「1，2句话总结。」等占位文字，不填就得清掉，
    否则占位符会原样出现在成品里。
    """
    block = find_shape(xml, name)
    if block is None:
        return xml
    paras = shape_paragraphs(block)
    keep = paras[0] if paras else "<a:p><a:endParaRPr/></a:p>"
    empty = RUN_RE.sub("", keep)
    empty = ENDPR_RE.sub("<a:endParaRPr/>", empty)
    if empty == keep:  # 模板段落没有 run，本身就是空段
        empty = keep
    body = TXBODY_RE.search(block).group(0)
    open_tag = body[: body.index("<a:p>")] if "<a:p>" in body else body[: -len("</p:txBody>")]
    return xml.replace(block, block.replace(body, open_tag + empty + "</p:txBody>"))


def remove_shape(xml, name):
    """删除形状（用于不该出现的空槽位）。"""
    block = find_shape(xml, name)
    if block is None:
        return xml
    return xml.replace(block, "")


def split_point(text):
    """把「标签：说明」拆成 (标签, 冒号及以后)；无标签时返回 (None, 全文)。"""
    for sep in ("：", ":"):
        i = text.find(sep)
        if i > 0:
            return text[:i], text[i:]
    return None, text


def page_title_size(text):
    """页标题自适应：模板基准 28 磅，超长时递减以免出框。"""
    n = len(text)
    if n <= 26:
        return 2800
    if n <= 34:
        return 2400
    return 2000


COVER_TITLE_PT = 44      # 封面标题默认字号（要求：微软雅黑 44 磅居中）
COVER_TITLE_FACE = ("微软雅黑", "Microsoft YaHei")


def cover_title_size(text):
    """封面标题自适应：默认 44 磅，标题过长时逐级下调，避免盖住副标题。"""
    n = len(text)
    if n <= 18:
        return COVER_TITLE_PT * 100
    if n <= 26:
        return 4000
    if n <= 34:
        return 3600
    return 3200


def _display_width(text):
    """CJK 计 1，ASCII 计 0.55，用来估一行能放下多少字。"""
    n = 0.0
    for ch in text or "":
        n += 0.55 if ord(ch) < 128 else 1.0
    return n


def _chars_per_line(box_w_cm, pt):
    char_w = 0.56 * (pt / 16.0)
    return max(4.0, box_w_cm / char_w)


def _line_h_cm(pt, spc_after_pt=8):
    return (pt * 1.2 + spc_after_pt) / 28.35


def _para_lines(text, box_w_cm, pt):
    cpl = _chars_per_line(box_w_cm, pt)
    width = _display_width(text) or 1.0
    return max(1, int((width + cpl - 0.01) / cpl))


def fit_font_pt(paragraphs, box_w_cm, box_h_cm, default_pt, min_pt, max_pt=None):
    """按框高选字号：默认字号能放下就用默认，否则降到 min_pt。"""
    max_pt = max_pt or default_pt
    usable_w = max(2.0, box_w_cm - BODY_INDENT_CM)
    for pt in range(max_pt, min_pt - 1, -1):
        lines = sum(_para_lines(p, usable_w, pt) for p in paragraphs) or 1
        if lines * _line_h_cm(pt) <= box_h_cm + 0.05:
            return pt
    return min_pt


def shape_xfrm(block):
    off = re.search(r'<a:off x="([^"]+)" y="([^"]+)"', block)
    ext = re.search(r'<a:ext cx="([^"]+)" cy="([^"]+)"', block)
    if not (off and ext):
        return None
    return {
        "x": int(off.group(1)),
        "y": int(off.group(2)),
        "cx": int(ext.group(1)),
        "cy": int(ext.group(2)),
    }


def set_shape_box(xml, name, x=None, y=None, cx=None, cy=None):
    """改形状位置或尺寸（EMU）。未给出的分量保持原值。"""
    block = find_shape(xml, name)
    if block is None:
        return xml
    xfrm = shape_xfrm(block)
    if xfrm is None:
        return xml
    nx = xfrm["x"] if x is None else int(x)
    ny = xfrm["y"] if y is None else int(y)
    ncx = xfrm["cx"] if cx is None else int(cx)
    ncy = xfrm["cy"] if cy is None else int(cy)
    new_block, n = re.subn(
        r'<a:off x="[^"]+" y="[^"]+"/>\s*<a:ext cx="[^"]+" cy="[^"]+"',
        '<a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"' % (nx, ny, ncx, ncy),
        block,
        count=1,
    )
    if not n:
        return xml
    return xml.replace(block, new_block)


def set_autofit(xml, name, tag="noAutofit"):
    """设置自动缩放方式。

    注意 <a:bodyPr/> 是自闭合的，直接往后插子标签会生成非法 XML
    （noAutofit 会变成 bodyPr 的兄弟节点）。必须先把它展开成
    <a:bodyPr><a:noAutofit/></a:bodyPr>。
    """
    block = find_shape(xml, name)
    if block is None:
        return xml
    # 1) 已经有 autofit 子标签：直接换掉
    new_block = re.sub(
        r"<a:(spAutoFit|normAutofit|noAutofit)\b[^>]*/>",
        "<a:%s/>" % tag,
        block,
        count=1,
    )
    if new_block == block:
        # 2) 自闭合的 <a:bodyPr/>：展开后放进去
        new_block = re.sub(
            r"<a:bodyPr([^>]*)/>",
            r"<a:bodyPr\1><a:%s/></a:bodyPr>" % tag,
            block,
            count=1,
        )
    if new_block == block:
        # 3) 带子元素的 <a:bodyPr ...>：插成第一个子元素
        new_block = re.sub(
            r"(<a:bodyPr[^>]*>)",
            r"\1<a:%s/>" % tag,
            block,
            count=1,
        )
    if new_block == block:
        return xml
    return xml.replace(block, new_block)


def next_cNvPr_id(xml):
    ids = [int(x) for x in re.findall(r'<p:cNvPr[^>]*\bid="(\d+)"', xml)]
    return (max(ids) if ids else 2) + 1


def pic_xml(shape_id, name, rid, xfrm, descr=""):
    descr_attr = ' descr="%s"' % esc(descr) if descr else ""
    return (
        "<p:pic><p:nvPicPr><p:cNvPr id=\"%d\" name=\"%s\"%s/>"
        "<p:cNvPicPr><a:picLocks noChangeAspect=\"0\"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>"
        "<p:blipFill><a:blip r:embed=\"%s\"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>"
        "<p:spPr><a:xfrm><a:off x=\"%d\" y=\"%d\"/><a:ext cx=\"%d\" cy=\"%d\"/></a:xfrm>"
        "<a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></p:spPr></p:pic>"
        % (shape_id, esc(name), descr_attr, rid, xfrm["x"], xfrm["y"], xfrm["cx"], xfrm["cy"])
    )


def replace_figure_with_pic(xml, png_rel_id, descr=""):
    """把图表槽矩形换成同一几何的图片（用于申请书原图）。"""
    block = find_shape(xml, SLOT_FIGURE)
    if block is None:
        return xml
    xfrm = shape_xfrm(block)
    if xfrm is None:
        return xml
    pic = pic_xml(next_cNvPr_id(xml), "图表", png_rel_id, xfrm, descr=descr)
    return xml.replace(block, pic)


def replace_figure_with_shapes(xml, shape_xml):
    """把图表槽矩形换成一组 PPT 原生形状——在 PowerPoint 里可以点开编辑。"""
    block = find_shape(xml, SLOT_FIGURE)
    if block is None or not shape_xml:
        return xml
    return xml.replace(block, shape_xml)


def figure_caption(figure):
    if isinstance(figure, dict):
        return figure.get("caption") or figure.get("title") or ""
    if isinstance(figure, str) and figure not in ("", FIG_PLACEHOLDER):
        if os.path.exists(figure):
            return ""
        return figure
    return ""


# --------------------------------------------------------------------------
# 逐页生成
# --------------------------------------------------------------------------
def build_slide(tpl_xml, page):
    """按页面类型把一页内容写进模板幻灯片 XML。"""
    kind = page["kind"]
    title = page.get("title", "")

    if kind == "cover":
        xml = fill_shape(
            tpl_xml,
            "标题 1",
            [(0, [(title, 0)])],
            sz=cover_title_size(title),
            typeface=COVER_TITLE_FACE,
        )
        xml = force_align(xml, "标题 1", "ctr")
        xml = set_autofit(xml, "标题 1", "noAutofit")
        subtitle = page.get("subtitle")
        if subtitle:
            xml = fill_shape(xml, "副标题 2", [(0, [(subtitle, 0)])])
        else:
            xml = remove_shape(xml, "副标题 2")
        presenter = page.get("presenter", "")
        date = page.get("date", "")
        org = page.get("org", "中国科学院软件研究所")
        xml = set_shape_box(
            xml,
            "文本框 3",
            x=COVER_META_X,
            y=COVER_META_Y,
            cx=COVER_META_CX,
            cy=COVER_META_CY,
        )
        # 三行：报告人 / 单位 / 日期。单位行单独降 2 磅，视觉上收在姓名之下。
        meta_specs = [
            (0, [("报告人：" + presenter, 0)]),
            (0, [(org, 0)]),
            (1, [(date, 0)]),
        ]
        xml = fill_shape_mixed(
            xml,
            "文本框 3",
            meta_specs,
            [COVER_META_PT, COVER_META_ORG_PT, COVER_META_PT],
            typeface=("楷体", "Times New Roman"),
        )
        xml = set_autofit(xml, "文本框 3", "noAutofit")
        return xml

    if kind == "closing":
        xml = tpl_xml
        if title:
            xml = fill_shape(xml, "标题 1", [(0, [(title, 0)])])
        summary = page.get("summary")
        if summary:
            xml = fill_shape(xml, "文本框 6", [(0, [(summary, 0)])])
        else:
            xml = remove_shape(xml, "文本框 6")
        thanks = page.get("thanks") or "感谢各位评审专家！"
        xml = fill_shape(xml, "文本框 3", [(0, [(thanks, 0)])])
        return xml

    # 正文页：积累 / 左文右图 / 左图右文
    xml = fill_shape(tpl_xml, SLOT_TITLE, [(0, [(title, 0)])], sz=page_title_size(title))

    figure = page.get("figure")
    pic_path = page.get("_figure_png")
    canvas = page.get("_figure_shapes")
    if canvas is not None and _shapes is not None:
        # 自己画的图 → 原生形状，坐标以图表槽左上角为原点
        block = find_shape(xml, SLOT_FIGURE)
        xfrm = shape_xfrm(block) if block is not None else None
        if xfrm is not None:
            xml = replace_figure_with_shapes(
                xml,
                _shapes.to_xml(canvas, xfrm["x"] * CM_PER_EMU, xfrm["y"] * CM_PER_EMU),
            )
    elif pic_path:
        xml = replace_figure_with_pic(xml, page["_figure_rid"], descr=figure_caption(figure))
    else:
        caption = figure_caption(figure) or FIG_PLACEHOLDER
        xml = fill_shape(
            xml,
            SLOT_FIGURE,
            [(0, [(caption, 0)])],
            sz=None if caption == FIG_PLACEHOLDER else 2000,
        )

    heading = page.get("heading")
    points = page.get("points") or []
    body_h_cm = 3.93
    body_w_cm = BODY_WIDTH_CM
    body_block = find_shape(xml, SLOT_BODY)
    if body_block:
        xfrm = shape_xfrm(body_block)
        if xfrm:
            aligned = align_body_and_figure(xfrm, kind)
            xml = set_shape_box(
                xml,
                SLOT_BODY,
                x=aligned["x"],
                y=aligned["y"],
                cx=aligned["cx"],
                cy=aligned["cy"],
            )
            body_h_cm = aligned["h_cm"]
            body_w_cm = aligned["w_cm"]
    paras = ([heading] if heading else []) + list(points)
    body_pt = fit_font_pt(
        paras, body_w_cm, body_h_cm, BODY_PT_DEFAULT, BODY_PT_MIN, BODY_PT_MAX
    )
    heading_pt = min(BODY_PT_MAX, body_pt + 2)
    page["_body_pt"] = body_pt

    specs = []
    if heading:
        specs.append((0, [(heading, 0)]))
    for point in points:
        label, rest = split_point(point)
        if label is None:
            specs.append((1, [(rest, 1)]))
        else:
            specs.append((1, [(label, 0), (rest, 1)]))
    if specs:
        filled = []
        heading_used = False
        for ptpl, runs in specs:
            sz = heading_pt if (ptpl == 0 and not heading_used) else body_pt
            if ptpl == 0:
                heading_used = True
            filled.append((ptpl, runs, sz))
        xml = fill_body(xml, filled)
        xml = set_autofit(xml, SLOT_BODY, "noAutofit")
    else:
        xml = clear_shape(xml, SLOT_BODY)

    summary = page.get("summary")
    if summary:
        sum_block = find_shape(xml, SLOT_SUMMARY)
        sum_w, sum_h = 30.55, 1.13
        if sum_block:
            xfrm = shape_xfrm(sum_block)
            if xfrm:
                sum_w = xfrm["cx"] * CM_PER_EMU
                sum_h = xfrm["cy"] * CM_PER_EMU
        # 模板总结框只有一行高（1.13 cm），下面紧挨着页码框，不能往下加高。
        # 所以总结句要写成一句话、一行放下；太长就降字号，并在校验里提示回去改短。
        sum_pt = fit_font_pt([summary], sum_w, sum_h, SUMMARY_PT_DEFAULT, SUMMARY_PT_MIN)
        page["_summary_pt"] = sum_pt
        xml = fill_shape(xml, SLOT_SUMMARY, [(0, [(summary, 0)])], sz=sum_pt * 100)
        xml = set_autofit(xml, SLOT_SUMMARY, "noAutofit")
    else:
        xml = clear_shape(xml, SLOT_SUMMARY)
    return xml


def align_body_and_figure(body_xfrm, kind):
    """正文框与图表槽对齐到同一条上沿、同一条下沿。

    模板里正文框比图表槽更靠下、更矮（text-left 差约 2 cm，figure-left 差约 2.5 cm），
    直接填充会一边高一边矮。这里把正文上沿抬到图表槽上沿，下沿收到总结框上方，
    两边视觉高度一致；左右位置按 FIG_GAP_CM 贴回图边。
    """
    fig_top = FIGURE_TOP.get(kind, body_xfrm["y"] * CM_PER_EMU)
    fig_bottom = FIGURE_BOTTOM.get(kind, fig_top + 9.03)
    summary_top = SUMMARY_TOP.get(kind, fig_bottom + 2.0)
    # 下沿取「图表槽下沿」与「总结框上方 0.3 cm」中较低的那个，避免压到总结句
    bottom = min(fig_bottom, summary_top - 0.3)
    top = fig_top
    h_cm = max(body_xfrm["cy"] * CM_PER_EMU, bottom - top)
    x_cm = body_xfrm["x"] * CM_PER_EMU
    w_cm = body_xfrm["cx"] * CM_PER_EMU
    fig_left = FIGURE_LEFT.get(kind)
    fig_right = FIGURE_RIGHT.get(kind)
    if fig_left is not None:
        # 文在左、图在右：正文右沿收到图表左沿之前，留出间隙
        w_cm = max(6.0, fig_left - BODY_RIGHT_GAP_CM - x_cm)
    elif fig_right is not None:
        # 图在左、文在右：正文左沿贴到图右沿之后，别让中间空一大条
        x_cm = fig_right + FIG_GAP_CM.get(kind, 1.5)
    return {
        "x": int(round(x_cm / CM_PER_EMU)),
        "y": int(round(top / CM_PER_EMU)),
        "cx": int(round(w_cm / CM_PER_EMU)),
        "cy": int(round(h_cm / CM_PER_EMU)),
        "h_cm": h_cm,
        "w_cm": w_cm,
    }


def fill_body(xml, filled):
    """filled: [(模板段落序号, runs, 磅), ...]"""
    block = find_shape(xml, SLOT_BODY)
    if block is None:
        raise KeyError("模板中找不到形状：%s" % SLOT_BODY)
    paras = shape_paragraphs(block)
    if not paras:
        raise KeyError("形状 %s 没有段落模板" % SLOT_BODY)
    new = "".join(
        build_paragraph(paras[min(ptpl, len(paras) - 1)], runs, sz * 100, None)
        for ptpl, runs, sz in filled
    )
    body = TXBODY_RE.search(block).group(0)
    open_tag = body[: body.index("<a:p>")] if "<a:p>" in body else body[: -len("</p:txBody>")]
    new_body = open_tag + new + "</p:txBody>"
    return xml.replace(block, block.replace(body, new_body))


def build_notes(tpl_notes_xml, text):
    """把旁白写进备注页。"""
    block = find_shape(tpl_notes_xml, "Text Placeholder 2")
    if block is None:
        raise KeyError("模板备注页缺少 Text Placeholder 2")
    paras = shape_paragraphs(block)
    tpl = paras[0] if paras else "<a:p><a:endParaRPr/></a:p>"
    lines = [ln for ln in (text or "").split("\n")]
    new = "".join(build_paragraph(tpl, [(ln, 0)]) for ln in lines)
    body = TXBODY_RE.search(block).group(0)
    new_body = body[: body.index("<a:p>")] + new + "</p:txBody>"
    return tpl_notes_xml.replace(block, block.replace(body, new_body))


# --------------------------------------------------------------------------
# 包级装配：幻灯片清单、关系、内容类型
# --------------------------------------------------------------------------
def rels_doc(items):
    """items: [(Id, 关系短名, Target)]"""
    parts = [XML_DECL, '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for rid, rtype, target in items:
        parts.append(
            '<Relationship Id="%s" Type="%s/%s" Target="%s"/>' % (rid, REL_BASE, rtype, target)
        )
    parts.append("</Relationships>")
    return "".join(parts)


CONTENT_TYPES = [
    ("/ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"),
    ("/ppt/slideMasters/", "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"),
    ("/ppt/slideLayouts/", "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"),
    ("/ppt/notesMasters/", "application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml"),
    ("/ppt/notesSlides/", "application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"),
    ("/ppt/slides/", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"),
    ("/ppt/handoutMasters/", "application/vnd.openxmlformats-officedocument.presentationml.handoutMaster+xml"),
    ("/ppt/theme/", "application/vnd.openxmlformats-officedocument.theme+xml"),
    ("/ppt/presProps.xml", "application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"),
    ("/ppt/viewProps.xml", "application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"),
    ("/ppt/tableStyles.xml", "application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"),
    ("/ppt/commentAuthors.xml", "application/vnd.openxmlformats-officedocument.presentationml.commentAuthors+xml"),
    ("/ppt/tags/", "application/vnd.openxmlformats-officedocument.presentationml.tags+xml"),
    ("/ppt/revisionInfo.xml", "application/vnd.ms-powerpoint.revisioninfo+xml"),
    ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
    ("/docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml"),
    ("/docProps/custom.xml", "application/vnd.openxmlformats-officedocument.custom-properties+xml"),
]
DEFAULTS = [
    ("jpeg", "image/jpeg"),
    ("png", "image/png"),
    ("wdp", "image/vnd.ms-photo"),
    ("rels", "application/vnd.openxmlformats-package.relationships+xml"),
    ("xml", "application/xml"),
]


def content_types_doc(parts):
    out = [XML_DECL, '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">']
    for ext, ct in DEFAULTS:
        out.append('<Default Extension="%s" ContentType="%s"/>' % (ext, ct))
    for path in sorted(parts):
        if not path.endswith(".xml") or path == "[Content_Types].xml" or "/_rels/" in path:
            continue
        if path.endswith(".rels"):
            continue
        for prefix, ct in CONTENT_TYPES:
            # Package part names in ``parts`` omit the leading slash, while
            # Content_Types override names include it (for example
            # ``ppt/presentation.xml`` vs ``/ppt/presentation.xml``).
            # Compare normalized names so every OOXML part receives its
            # required Override declaration.
            normalized_prefix = prefix.lstrip("/")
            if path == normalized_prefix or (
                normalized_prefix.endswith("/")
                and path.startswith(normalized_prefix)
            ):
                out.append('<Override PartName="/%s" ContentType="%s"/>' % (path, ct))
                break
    out.append("</Types>")
    return "".join(out)


def app_doc(n_slides, n_notes):
    return (
        XML_DECL
        + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"'
        ' xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        "<Application>Microsoft Office PowerPoint</Application>"
        "<PresentationFormat>宽屏</PresentationFormat>"
        "<Slides>%d</Slides><Notes>%d</Notes><HiddenSlides>0</HiddenSlides><MMClips>0</MMClips>"
        "<ScaleCrop>false</ScaleCrop><LinksUpToDate>false</LinksUpToDate><SharedDoc>false</SharedDoc>"
        "<HyperlinksChanged>false</HyperlinksChanged><AppVersion>16.0000</AppVersion>"
        "</Properties>" % (n_slides, n_notes)
    )


def set_doc_title(core_xml, title):
    """更新 docProps/core.xml 的 dc:title。"""
    if "<dc:title>" not in core_xml:
        return core_xml
    return re.sub(
        r"<dc:title>.*?</dc:title>",
        "<dc:title>%s</dc:title>" % esc(title),
        core_xml,
        flags=re.S,
    )


def build(content, template_path, out_path, narration_out=None):
    zin = zipfile.ZipFile(template_path)
    parts = {n: zin.read(n) for n in zin.namelist()}
    zin.close()

    pages = content["pages"]
    narration = content.get("narration") or {}

    def narr(i):
        if isinstance(narration, dict):
            return narration.get(str(i)) or narration.get(i) or ""
        if isinstance(narration, list) and i - 1 < len(narration):
            return narration[i - 1] or ""
        return ""

    tpl_slides, tpl_notes = {}, {}
    for name in parts:
        m = re.match(r"ppt/slides/slide(\d+)\.xml$", name)
        if m:
            tpl_slides[int(m.group(1))] = strip_decl(parts[name].decode("utf-8"))
        m = re.match(r"ppt/notesSlides/notesSlide(\d+)\.xml$", name)
        if m:
            tpl_notes[int(m.group(1))] = strip_decl(parts[name].decode("utf-8"))

    notes_tpl = tpl_notes[min(tpl_notes)]
    out_slides, out_notes, out_slide_rels, out_notes_rels = {}, {}, {}, {}
    generated_media = {}
    media_dir = os.path.join(os.path.dirname(os.path.abspath(out_path)), "_ppt_media")

    for i, page in enumerate(pages, start=1):
        kind = page["kind"]
        if kind not in BLUEPRINTS:
            raise SystemExit('第 %d 页的 kind "%s" 不受支持，可选：%s' % (i, kind, "/".join(BLUEPRINTS)))
        bp = BLUEPRINTS[kind]

        fig_rid = None
        fig_kind, fig_payload = prepare_figure(page, i, media_dir)
        if fig_kind == "png" and fig_payload:
            media_name = "generated_slide%d.png" % i
            generated_media["ppt/media/" + media_name] = fig_payload
            fig_rid = "rIdFig"
            page["_figure_png"] = fig_payload
            page["_figure_rid"] = fig_rid
        elif fig_kind == "shapes":
            page["_figure_shapes"] = fig_payload

        xml = build_slide(tpl_slides[bp], page)
        assert_xml_wellformed(xml, "第%d页" % i)
        out_slides[i] = XML_DECL + xml

        # 幻灯片关系：沿用模板蓝图的关系（版式、图片），只把备注页指向本页
        bp_rels = parts["ppt/slides/_rels/slide%d.xml.rels" % bp].decode("utf-8")
        items = []
        for rid, rtype, target in re.findall(
            r'<Relationship Id="([^"]+)" Type="[^"]*/(\w+)" Target="([^"]+)"', bp_rels
        ):
            if rtype == "notesSlide":
                continue
            items.append((rid, rtype, target))
        used = {rid for rid, _, _ in items}
        nid = 1
        while ("rId%d" % nid) in used or ("rId%d" % nid) == fig_rid:
            nid += 1
        items.append(("rId%d" % nid, "notesSlide", "../notesSlides/notesSlide%d.xml" % i))
        if fig_rid:
            items.append((fig_rid, "image", "../media/%s" % ("generated_slide%d.png" % i)))
        out_slide_rels[i] = rels_doc(items)

        # 备注页：模板备注页 + 本页旁白，回指本页幻灯片
        out_notes[i] = XML_DECL + build_notes(notes_tpl, narr(i))
        out_notes_rels[i] = rels_doc(
            [
                ("rId1", "notesMaster", "../notesMasters/notesMaster1.xml"),
                ("rId2", "slide", "../slides/slide%d.xml" % i),
            ]
        )

    # ---- 清理：模板中多余的幻灯片/备注页/修订记录/缩略图 ----
    keep = set(parts)
    for name in list(keep):
        if re.match(r"ppt/slides/slide\d+\.xml$", name) or re.match(
            r"ppt/slides/_rels/slide\d+\.xml\.rels$", name
        ):
            keep.discard(name)
        if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", name) or re.match(
            r"ppt/notesSlides/_rels/notesSlide\d+\.xml\.rels$", name
        ):
            keep.discard(name)
        if name.startswith("ppt/changesInfos/") or name == "docProps/thumbnail.jpeg":
            keep.discard(name)
    for i in range(1, len(pages) + 1):
        keep.add("ppt/slides/slide%d.xml" % i)
        keep.add("ppt/slides/_rels/slide%d.xml.rels" % i)
        keep.add("ppt/notesSlides/notesSlide%d.xml" % i)
        keep.add("ppt/notesSlides/_rels/notesSlide%d.xml.rels" % i)

    # ---- presentation.xml：重建 sldIdLst ----
    pres = parts["ppt/presentation.xml"].decode("utf-8")
    pres_rels_raw = parts["ppt/_rels/presentation.xml.rels"].decode("utf-8")
    pres_rels = [
        (rid, rtype, target)
        for rid, rtype, target in re.findall(
            r'<Relationship Id="([^"]+)" Type="[^"]*/(\w+)" Target="([^"]+)"', pres_rels_raw
        )
        if rtype not in ("slide", "changesInfo", "thumbnail", "customXml")
    ]
    max_id = max([
        int(m.group(1))
        for m in re.finditer(r'Id="rId(\d+)"', pres_rels_raw)
    ] or [0])
    slide_rels = []
    for i in range(1, len(pages) + 1):
        rid = "rId%d" % (max_id + i)
        slide_rels.append((rid, "slide", "slides/slide%d.xml" % i))
    pres_rels.extend(slide_rels)

    sld_id_lst = "<p:sldIdLst>%s</p:sldIdLst>" % "".join(
        '<p:sldId id="%d" r:id="%s"/>' % (255 + i, rid) for i, (rid, _, _) in enumerate(slide_rels, start=1)
    )
    pres = re.sub(r"<p:sldIdLst>.*?</p:sldIdLst>", sld_id_lst, pres, flags=re.S)
    parts["ppt/presentation.xml"] = pres.encode("utf-8")
    parts["ppt/_rels/presentation.xml.rels"] = rels_doc(pres_rels).encode("utf-8")

    # 根关系：模板缩略图指向已删除的部件，去掉这条关系
    root_rels = [
        (rid, rtype, target)
        for rid, rtype, target in re.findall(
            r'<Relationship Id="([^"]+)" Type="[^"]*/(\w+)" Target="([^"]+)"',
            parts["_rels/.rels"].decode("utf-8"),
        )
        if rtype != "thumbnail"
    ]
    parts["_rels/.rels"] = rels_doc(root_rels).encode("utf-8")

    # ---- 写入全部部件 ----
    for i in range(1, len(pages) + 1):
        parts["ppt/slides/slide%d.xml" % i] = out_slides[i].encode("utf-8")
        parts["ppt/slides/_rels/slide%d.xml.rels" % i] = out_slide_rels[i].encode("utf-8")
        parts["ppt/notesSlides/notesSlide%d.xml" % i] = out_notes[i].encode("utf-8")
        parts["ppt/notesSlides/_rels/notesSlide%d.xml.rels" % i] = out_notes_rels[i].encode("utf-8")
    for zip_name, src in generated_media.items():
        with open(src, "rb") as fh:
            parts[zip_name] = fh.read()
        keep.add(zip_name)

    parts.pop("[Content_Types].xml", None)
    for name in list(parts):
        if name not in keep and name != "[Content_Types].xml":
            parts.pop(name)
    parts["[Content_Types].xml"] = content_types_doc(parts).encode("utf-8")

    if "docProps/app.xml" in parts:
        parts["docProps/app.xml"] = app_doc(len(pages), len(pages)).encode("utf-8")
    if "docProps/core.xml" in parts and content.get("title"):
        parts["docProps/core.xml"] = set_doc_title(
            parts["docProps/core.xml"].decode("utf-8"), content["title"]
        ).encode("utf-8")

    order = ["[Content_Types].xml"] + [n for n in sorted(parts) if n != "[Content_Types].xml"]
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in order:
            zout.writestr(name, parts[name])

    if narration_out:
        write_narration(pages, narr, narration_out)
    return report(pages, narr, out_path)


def prepare_figure(page, index, media_dir):
    """把 page.figure 变成可插入的东西。

    返回 (kind, payload)：
      ("shapes", canvas) —— 自己画的图，生成 PPT 原生形状，在 PowerPoint 里可编辑
      ("png", path)      —— 申请书原图（type: image），按位图嵌入
      (None, None)       —— 画不出，由调用方退回文字占位

    自己画的图优先走 shapes；shapes 认不出的类型再退回旧的位图渲染。
    """
    fig = page.get("figure")
    if fig is None or fig == "" or fig == FIG_PLACEHOLDER:
        return None, None
    spec = fig
    if isinstance(fig, str):
        if os.path.exists(fig):
            spec = {"type": "image", "path": fig}
        else:
            return None, None
    if _shapes is None:
        return None, None
    spec = _shapes.normalize_figure(spec)
    if spec is None:
        return None, None

    # 申请书原图：本来就是一张图，按位图嵌入
    if isinstance(spec, dict) and spec.get("type") == "image":
        os.makedirs(media_dir, exist_ok=True)
        dest = os.path.join(media_dir, "slide%d.png" % index)
        try:
            path = _shapes.render_image(spec, dest)
        except Exception as exc:
            print("  [校验] 第%d页原图嵌入失败：%s" % (index, exc), file=sys.stderr)
            return None, None
        return ("png", path) if path else (None, None)

    # 自己画的图：生成原生形状，保证在 PowerPoint 里可编辑
    canvas = _shapes.render_shapes(spec)
    if canvas is not None and canvas.count():
        return "shapes", canvas
    return None, None


def write_narration(pages, narr, path):
    total = 0
    lines = []
    for i, page in enumerate(pages, start=1):
        text = narr(i)
        total += len(text)
        lines.append("### 第%d页 旁白（%d字）\n\n%s\n" % (i, len(text), text or "（未提供）"))
    lines.append("**旁白总字数：%d 字（约 %.1f 分钟）**" % (total, total / 200.0))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def check_content_rules(index, page):
    """把写进 content-spec 的硬规则变成脚本检查，别只停在文档里。"""
    out = []
    kind = page.get("kind")
    title = page.get("title") or ""
    points = page.get("points") or []
    if kind == "cover":
        if not (page.get("presenter") or "").strip():
            out.append("第%d页：封面缺 `presenter`，右下角会只剩一个「报告人：」" % index)
        if not (page.get("date") or "").strip():
            out.append("第%d页：封面缺 `date`，右下角日期会空着" % index)
        return out
    if kind == "accumulation" and points and len(points) != ACCUMULATION_POINTS:
        out.append(
            "第%d页：第 2 页要点应固定写 %d 条（发表论文 / 项目经验 / 合作厂商 / 编译优化），"
            "当前 %d 条" % (index, ACCUMULATION_POINTS, len(points))
        )
    summary = page.get("summary") or ""
    if summary:
        # 研究内容页：「针对…已有方法…不足…研究…技术」
        if title.startswith("研究内容") and "针对" not in summary:
            out.append("第%d页：研究内容页总结句没套模板。应为「针对〈问题〉，"
                       "已有方法〈不足〉，研究〈技术〉」，当前是：%s" % (index, summary[:20]))
        # 技术路线页：「提出/设计…相比于原有…优势/创新点在于…」
        if title.startswith("研究技术路线"):
            if not any(k in summary for k in ("提出", "设计")):
                out.append("第%d页：技术路线页总结句没套模板。应以「提出/设计…方法」开头，"
                           "当前是：%s" % (index, summary[:20]))
            elif not any(k in summary for k in ("相比", "优势", "创新")):
                out.append("第%d页：技术路线页总结句缺「相比于原有…，其优势在于…」这半句，"
                           "当前是：%s" % (index, summary[:20]))
    return out


def check_figure_fit(index, page):
    """图表选型自检：图要对得上这一页，而且不能是换个壳的文字罗列。"""
    out = []
    fig = page.get("figure")
    if not isinstance(fig, dict):
        return out
    ftype = fig.get("type") or ""
    title = page.get("title") or ""
    kind = page.get("kind")
    if kind == "text-left" and ftype in ("compare", "table", "cards"):
        out.append(
            "第%d页：需求/现状页用了 %s。这类页要读原文画逻辑链（type: logic），"
            "把要点分成两栏摆上去不算图；确要沿用申请书原图时才改用 image" % (index, ftype)
        )
    if "技术路线" in title and ftype != "image":
        # 用申请书原图的（image）不算问题——原图优先本来就是规范里的第一条路。
        if ftype != "flow":
            out.append("第%d页：技术路线页建议用 flow，当前是 %s" % (index, ftype or "空"))
        elif not any((s.get("children") or s.get("desc")) for s in (fig.get("steps") or []) if isinstance(s, dict)):
            out.append(
                "第%d页：技术路线图只有光秃秃的步骤框。要读「研究思路方法与技术路线」"
                "那几段，给每步挂上 children 子步骤" % index
            )
    if kind == "figure-left" and not (page.get("points") or page.get("heading")):
        out.append("第%d页：只有图没有文字要点，图文要都有" % index)
    return out


def report(pages, narr, out_path):
    print("输出文件：%s" % out_path)
    print("总页数：%d" % len(pages))
    warn = []
    for i, page in enumerate(pages, start=1):
        title = page.get("title") or page.get("kind")
        points = page.get("points") or []
        n_chars = sum(len(p) for p in points)
        line = "  第%2d页 [%-12s] %s" % (i, page.get("kind"), title)
        line += "  要点%d条/%d字" % (len(points), n_chars)
        if page.get("_body_pt"):
            line += "  正文%d磅" % page["_body_pt"]
        if page.get("summary"):
            line += "  总结%d字" % len(page["summary"])
            if page.get("_summary_pt"):
                line += "/%d磅" % page["_summary_pt"]
        if page.get("_figure_shapes") is not None:
            line += "  形状图%d个（可编辑）" % page["_figure_shapes"].count()
        elif page.get("_figure_png"):
            line += "  原图"
        nar = narr(i)
        if nar:
            line += "  旁白%d字" % len(nar)
        print(line)
        if len(points) > MAX_POINTS:
            warn.append("第%d页：要点 %d 条，超过 %d 条上限" % (i, len(points), MAX_POINTS))
        pt_limit = point_char_limit(page)
        for p in points:
            if len(p) > pt_limit:
                warn.append("第%d页：要点「%s」%d 字，超过 %d 字建议上限（已靠降字号消化）" % (i, p[:12], len(p), pt_limit))
        if page.get("_summary_pt") and page["_summary_pt"] < SUMMARY_READABLE_PT:
            warn.append(
                "第%d页：总结句压到 %d 磅。总结框只有一行高，请把它改短到 %d 字以内"
                % (i, page["_summary_pt"], SUMMARY_ONE_LINE_CHARS)
            )
        sum_limit = summary_char_limit(page)
        if page.get("summary") and len(page["summary"]) > sum_limit:
            warn.append(
                "第%d页：总结句 %d 字，超过 %d 字建议上限（已靠降字号消化）"
                % (i, len(page["summary"]), sum_limit)
            )
        warn.extend(check_content_rules(i, page))
        warn.extend(check_figure_fit(i, page))
        fig = page.get("figure")
        has_fig = page.get("_figure_png") or page.get("_figure_shapes") is not None
        if fig and fig != FIG_PLACEHOLDER and not has_fig:
            if isinstance(fig, dict) or (isinstance(fig, str) and os.path.exists(fig)):
                warn.append("第%d页：图表未成功嵌入，槽位退回文字说明" % i)
    total = sum(len(narr(i)) for i in range(1, len(pages) + 1))
    if total:
        print("旁白总字数：%d 字（目标 %d–%d 字，约 %.1f 分钟）" % (total, NARRATION_MIN, NARRATION_MAX, total / 200.0))
        if not (NARRATION_MIN <= total <= NARRATION_MAX):
            warn.append("旁白总字数 %d 字，超出 %d–%d 字" % (total, NARRATION_MIN, NARRATION_MAX))
    else:
        warn.append("未提供旁白，备注页为空")
    for w in warn:
        print("  [校验] %s" % w)
    return warn


def main():
    ap = argparse.ArgumentParser(description="把逐页内容填进参考模板，生成答辩 PPT")
    ap.add_argument("--content", required=True, help="内容 JSON 路径")
    ap.add_argument("--out", required=True, help="输出 pptx 路径")
    ap.add_argument("--template", default=None, help="模板 pptx 路径（默认 assets/template.pptx）")
    ap.add_argument("--narration-out", default=None, help="可选：旁白稿 Markdown 输出路径")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    template = args.template or os.path.join(here, "..", "assets", "template.pptx")
    if not os.path.exists(template):
        raise SystemExit("找不到模板：%s" % template)
    if not os.path.exists(args.content):
        raise SystemExit("找不到内容文件：%s" % args.content)

    with open(args.content, encoding="utf-8") as fh:
        content = json.load(fh)
    if not content.get("pages"):
        raise SystemExit("内容 JSON 缺少 pages 数组")

    build(content, template, args.out, args.narration_out)


if __name__ == "__main__":
    main()
