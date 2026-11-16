#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdf2ppt 模板填充器。

把 content.json 的逐页内容填进 assets/template.pptx 的固定版式，输出可直接放映、
可继续编辑的论文总结 PPT（旁白写入 PowerPoint 备注页）。

模板含 5 张蓝图（cover / text-left / status / figure-left / closing），
同一 kind 的页可无限复用，论文方法、实验要几页就给几页。
文本填进既有槽位；图表槽把模板的「图/表」色块换成论文原图（位图嵌入）
或按主题色绘制的原生形状。正文字号按文本框几何与字数在 12–18 磅之间自适应，
不改母版/背景/配色。

图表绘制需要 Pillow；未安装时图表槽退回文字说明，并在校验里提示。

用法：
    python3 build_ppt.py --content content.json --out 论文总结PPT.pptx
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
# 模板只有 5 张蓝图，按 kind 取蓝图；同一 kind 的页可无限复用，
# 论文方法讲几步、实验讲几组，都不需要改模板。
BLUEPRINTS = {"cover": 1, "text-left": 2, "status": 3, "figure-left": 4, "closing": 5,
              "refs": 2}  # 参考文献页复用 text-left 蓝图：删图槽、正文拉通全宽
# 每类页的正文槽、总结槽、图槽（按模板蓝图页里标注的图序号）。
KIND_SLOTS = {
    "text-left":   {"body": "文本框 31", "summary": "文本框 33", "figures": ["矩形 2"]},
    "status":      {"body": "文本框 31", "summary": None,       "figures": ["矩形 2", "矩形 8"]},
    "figure-left": {"body": "文本框 31", "summary": "文本框 33", "figures": ["矩形 2"]},
}
SLOT_TITLE = "文本框 1"
SLOT_FIGURE = "矩形 2"
SLOT_BODY = "文本框 31"
SLOT_SUMMARY = "文本框 33"
# 一级标题楷体 18 磅，二级楷体 16 磅，按模板段落的缩进，不再自适应缩放
LEVEL1_PT = 18
LEVEL2_PT = 16
BULLET_L1 = "▶"   # 一级要点项目符号（实心三角）
BULLET_L2 = "●"   # 二级要点项目符号（实心圆点）
BODY_FACE = ("楷体", "楷体")
FIG_PLACEHOLDER = "图/表"

REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

# 版式自查阈值（依据 references/template-spec.md 的实测几何推算）
MAX_POINTS = 6
# 标题式要点：一级主句必须是短语，不是句子（参照 proposalword2ppt 的写作形式）
MAX_POINT_CHARS = 20
# 现状页等要带原文引用 [N]，ASCII 编号不计满宽，放宽一点
STATUS_POINT_CHARS = 24
# 二级行：短说明（数字/机制落点），挂在一级标题下面
SUB_CHARS = 22
MAX_SUBS = 3
SUB_PT_DROP = 2          # 二级行比一级标题小 2 磅
SUB_INDENT_CM = 0.85     # 二级行在一级标题基础上再缩进
MAX_FIGURES = 4           # 一页图数以模板槽位为准，模板最多四个矩形
MAX_SUMMARY_CHARS = 60
# 概述/方法/实验页的总结句要把本页结论说完整，本身比普通判断句长
TEMPLATE_SUMMARY_CHARS = 74
CLOSING_SUMMARY_CHARS = 90  # 结束语收尾句要写足单位优势，允许更长


# 论文总结的内容页都要写清「原理/结论 + 依据」，主句统一放宽到 44 字
LONG_POINT_KINDS = ("text-left", "status", "figure-left")


def point_char_limit(page):
    if page.get("kind") in LONG_POINT_KINDS:
        return STATUS_POINT_CHARS
    return MAX_POINT_CHARS


def disp_width(text):
    """显示宽度：CJK 记 1、ASCII 记 0.55；先去掉 {{}}/**'' 标记。"""
    t = strip_emph(text)
    return sum(1.0 if ord(ch) > 0x2E7F else 0.55 for ch in t)


def summary_char_limit(page):
    if page.get("kind") == "closing":
        return CLOSING_SUMMARY_CHARS
    if page.get("kind") in ("text-left", "figure-left"):
        return TEMPLATE_SUMMARY_CHARS
    return MAX_SUMMARY_CHARS
# 15 分钟演讲：按每分钟 200 字折算 13–15 分钟
NARRATION_MIN = 2600
NARRATION_MAX = 3000
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
# 图表槽左沿（cm，取自 assets/template.pptx 实测）。
# text-left 的图槽在右侧 14.58 起，正文右沿必须收到它前面 0.4 cm。
FIGURE_LEFT = {
    "text-left": 14.58,
    "status": 13.13,
}
BODY_RIGHT_GAP_CM = 0.40
FIGURE_BOTTOM = {
    "text-left": 14.16,
    "status": 18.60,
    "figure-left": 14.16,
}
FIGURE_TOP = {
    "text-left": 4.89,
    "status": 2.31,
    "figure-left": 4.89,
}
# 总结框上沿；status 页没有总结槽，正文下沿直接收到图槽底
SUMMARY_TOP = {
    "text-left": 17.05,
    "figure-left": 17.05,
}
COVER_META_PT = 20        # 封面「汇报人 / 单位 / 日期」三行
# 封面右下角文本框：模板框偏小，三行 20 磅放不下，这里整体加高
COVER_META_X = int(round(21.2 / CM_PER_EMU))
COVER_META_Y = int(round(14.75 / CM_PER_EMU))
COVER_META_CX = int(round(11.4 / CM_PER_EMU))
COVER_META_CY = int(round(3.35 / CM_PER_EMU))
# 图在左、字在右时，正文左沿按「图右沿 + 间隙」定，贴回图边。
# 模板把正文放在 23.06，离图 4 cm，中间空一大条。
FIGURE_RIGHT = {
    "figure-left": 19.29,
}
FIG_GAP_CM = {
    "figure-left": 1.0,
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


def drop_underline(rpr):
    """去掉 rPr 的下划线属性（正文只允许标签带下划线）。"""
    if rpr is None:
        return None
    return re.sub(r'\su="[^"]*"', "", rpr)


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


def build_paragraph(para_tpl, runs, sz=None, typeface=None, bullet=None):
    """克隆模板段落，替换其中的文本 run。

    bullet 给正文要点用：{"char": "▶", "marL_cm": 1.0, "hang_cm": 1.0}
    —— 整段 pPr 重写为「项目符号 + 悬挂缩进」，marL_cm 是文字起始位置，
    符号挂在 marL-hang_cm 处。None 则保留模板 pPr 原样（标题/备注等）。
    正文只有标签 run（style 带 keep_u）保留下划线，其余 run 一律去掉。
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

    if bullet:
        # 模板段落自带的 pPr（marL/indent/buNone 等）整体换成我们的项目符号格式
        body = re.sub(r"<a:pPr\b[^>]*/>|<a:pPr\b.*?</a:pPr>", "", body, count=1, flags=re.S)
        marl = int(round(bullet["marL_cm"] / CM_PER_EMU))
        hang = int(round(bullet["hang_cm"] / CM_PER_EMU))
        ppr = ('<a:pPr marL="%d" indent="-%d">'
               '<a:buFont typeface="Arial" pitchFamily="34" charset="0"/>'
               '<a:buChar char="%s"/></a:pPr>' % (marl, hang, bullet["char"]))
        body = body.replace("<a:p>", "<a:p>" + ppr, 1)

    out_runs = []
    for item in runs:
        # runs 支持二元组 (文本, run序号) 或三元组 (文本, run序号, 强调样式)
        text, idx, style = (item + (None,))[:3] if len(item) == 2 else item
        rpr = rprs[min(idx, len(rprs) - 1)]
        if rpr is not None:
            rpr = norm_lang(rpr)
            if sz is not None:
                rpr = set_sz(rpr, sz)
            if typeface is not None:
                rpr = set_typeface(rpr, *typeface)
            if not (style and style.get("keep_u")):
                rpr = drop_underline(rpr)
            rpr = style_rpr(rpr, style)
            out_runs.append("<a:r>%s<a:t>%s</a:t></a:r>" % (rpr, esc(text)))
        else:
            if style and style.get("color"):
                rpr = ('<a:rPr lang="zh-CN" b="1"><a:solidFill>'
                       '<a:srgbClr val="%s"/></a:solidFill></a:rPr>' % style["color"])
            elif style and style.get("bold"):
                rpr = '<a:rPr lang="zh-CN" b="1"/>'
            else:
                rpr = None
            if rpr:
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


# 重点标记：{{...}} 渲染为红色加粗，**...** 渲染为加粗。写到 check_ppt 的规则：
# 每个内容页至少一处 {{...}}，关键数字/方法名/结论词用它标出来。
EMPH_RE = re.compile(r"\{\{(.+?)\}\}|\*\*(.+?)\*\*")
EMPH_RED = "C00000"


def strip_emph(text):
    """去掉重点标记，得到量长度/校对用的纯文本。"""
    return EMPH_RE.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(2), text or "")


def rich_runs(text, base_idx=0):
    """把含 {{红粗}}/**粗体** 的文本拆成 [(文本, run_idx, 样式dict|None), ...]。"""
    out, pos = [], 0
    for m in EMPH_RE.finditer(text or ""):
        if m.start() > pos:
            out.append((text[pos:m.start()], base_idx, None))
        if m.group(1) is not None:
            out.append((m.group(1), base_idx, {"bold": True, "color": EMPH_RED}))
        else:
            out.append((m.group(2), base_idx, {"bold": True}))
        pos = m.end()
    if pos < len(text or ""):
        out.append((text[pos:], base_idx, None))
    return out or [(text or "", base_idx, None)]


def style_rpr(rpr, style):
    """在模板 run 属性上叠加强调样式：b="1" 与红色 solidFill。"""
    if not style or rpr is None:
        return rpr
    bold, color = style.get("bold"), style.get("color")
    if bold and "<a:rPr" in rpr:
        if re.search(r"<a:rPr[^>]*\bb=", rpr) is None:
            rpr = re.sub(r"(<a:rPr[^>]*?)(/?>)", r'\1 b="1"\2', rpr, count=1)
    if color:
        fill = '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % color
        if "<a:solidFill>" not in rpr:
            if "/>" in rpr[: rpr.index(">") + 1] and rpr.rstrip().endswith("/>"):
                # 自闭合：<a:rPr .../> → 展开并插入填充
                head = rpr.rstrip()[:-2]
                rpr = head + ">" + fill + "</a:rPr>"
            else:
                rpr = re.sub(r"(<a:rPr[^>]*>)", r"\1" + fill, rpr, count=1)
    return rpr


def normalize_point(point):
    """要点统一成 {label, text, subs}。

    旧写法是字符串「标签：说明」，继续接受。新写法是对象，subs 是挂在
    一级标题下的二级行（不足、方法、原理），每条独立成段。
    """
    if isinstance(point, str):
        label, rest = split_point(point)
        return {"label": label, "text": rest if label else point, "subs": []}
    if isinstance(point, dict):
        label = (point.get("label") or "").strip() or None
        text = point.get("text")
        if text is None:
            text = point.get("body") or ""
        text = str(text).strip()
        subs = []
        for sub in (point.get("subs") or [])[:MAX_SUBS]:
            line = sub.get("text") if isinstance(sub, dict) else sub
            line = str(line or "").strip()
            if line:
                subs.append(line)
        return {"label": label, "text": text, "subs": subs}
    return {"label": None, "text": str(point), "subs": []}


def point_text(point):
    """校验和字数统计用的纯文本。"""
    item = normalize_point(point)
    parts = []
    if item["label"]:
        parts.append(item["label"] + "：" + item["text"])
    elif item["text"]:
        parts.append(item["text"])
    parts.extend(item["subs"])
    return "".join(parts)


def normalize_figures(figure, limit=None):
    """figure 可以是一个对象，也可以是一组对象。limit 用这一页模板的槽位数。"""
    if figure is None or figure == "" or figure == FIG_PLACEHOLDER:
        return []
    if isinstance(figure, list):
        figs = [f for f in figure if f not in (None, "", FIG_PLACEHOLDER)]
        return figs[:limit] if limit else figs
    return [figure]


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
    """改形状位置或尺寸（EMU）。未给出的分量保持原值。

    形状自己没有 xfrm（占位符继承版式）时，在 spPr 里新建一份显式 xfrm——
    继承几何看不见也改不了，显式写出来才能被预览与后续编辑感知。
    """
    block = find_shape(xml, name)
    if block is None:
        return xml
    xfrm = shape_xfrm(block)
    if xfrm is None:
        # 从版式继承的占位符：用版式的值兜底（调用方至少给了要改的分量）
        lx, ly, lcx, lcy = 0, 0, 0, 0
        m = re.search(r'<p:spPr\s*/?>', block)
        xfrm_xml = '<a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>' % (
            int(x or lx), int(y or ly), int(cx or lcx), int(cy or lcy))
        if m:
            if m.group(0).endswith("/>"):
                new_spPr = '<p:spPr>%s</p:spPr>' % xfrm_xml
                new_block = block.replace(m.group(0), new_spPr, 1)
            else:
                new_block = block.replace(m.group(0), m.group(0) + xfrm_xml, 1)
        else:
            # 没有 spPr：插到 nvSpPr 之后
            m2 = re.search(r"</p:nvSpPr>", block)
            if not m2:
                return xml
            new_block = block.replace(m2.group(0), m2.group(0) + "<p:spPr>%s</p:spPr>" % xfrm_xml, 1)
        return xml.replace(block, new_block)
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


def _fit_pic_xfrm(page, pic_index, slot_xfrm):
    """按图片真实宽高比，在图槽内等比放到最大并居中（配合 render_image 去白边）。"""
    pics = page.get("_figure_pics") or []
    path = pics[pic_index][2] if pic_index < len(pics) and len(pics[pic_index]) > 2 else None
    if path is None:
        return slot_xfrm
    try:
        from PIL import Image
        with Image.open(path) as im:
            w_px, h_px = im.size
    except Exception:
        return slot_xfrm
    slot_w = slot_xfrm["cx"] * CM_PER_EMU
    slot_h = slot_xfrm["cy"] * CM_PER_EMU
    if w_px <= 0 or h_px <= 0:
        return slot_xfrm
    scale = min(slot_w / w_px, slot_h / h_px)
    w_cm, h_cm = w_px * scale, h_px * scale
    x_cm = slot_xfrm["x"] * CM_PER_EMU + (slot_w - w_cm) / 2.0
    y_cm = slot_xfrm["y"] * CM_PER_EMU + (slot_h - h_cm) / 2.0
    return {"x": int(round(x_cm / CM_PER_EMU)), "y": int(round(y_cm / CM_PER_EMU)),
            "cx": int(round(w_cm / CM_PER_EMU)), "cy": int(round(h_cm / CM_PER_EMU))}


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


def split_slot(xfrm, n):
    """把图表槽切成 n 个子槽，返回的坐标是厘米。2 组上下分，3 组上一下二。"""
    n = max(1, n)
    x = xfrm["x"] * CM_PER_EMU
    y = xfrm["y"] * CM_PER_EMU
    cx = xfrm["cx"] * CM_PER_EMU
    cy = xfrm["cy"] * CM_PER_EMU
    if n == 1:
        return [{"x": x, "y": y, "cx": cx, "cy": cy}]
    gap = 0.30
    if n == 2:
        h = (cy - gap) / 2.0
        return [
            {"x": x, "y": y, "cx": cx, "cy": h},
            {"x": x, "y": y + h + gap, "cx": cx, "cy": cy - h - gap},
        ]
    top_h = cy * 0.56
    bot_h = cy - top_h - gap
    half = (cx - gap) / 2.0
    return [
        {"x": x, "y": y, "cx": cx, "cy": top_h},
        {"x": x, "y": y + top_h + gap, "cx": half, "cy": bot_h},
        {"x": x + half + gap, "y": y + top_h + gap, "cx": cx - half - gap, "cy": bot_h},
    ]


def ordered_figure_slots(page):
    """按 content.json 里 figure 数组的原始顺序，产出 ('canvas', i) / ('pic', j)。

    prepare_figure 把形状图和位图分进两个列表，顺序信息丢了；多图切分/多槽
    放置必须按作者给的数组顺序对应格位（主结果图在前就放上半格）。
    """
    out = []
    ci = pj = 0
    canvases = page.get("_figure_shapes") or []
    pics = page.get("_figure_pics") or []
    for fig in normalize_figures(page.get("figure")):
        is_img = isinstance(fig, str) or (isinstance(fig, dict) and fig.get("type") == "image")
        if is_img:
            if pj < len(pics):
                out.append(("pic", pj))
                pj += 1
        elif ci < len(canvases):
            out.append(("canvas", ci))
            ci += 1
    return out


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
        if not (page.get("subtitle") or "").strip():
            page["subtitle"] = "论文总结汇报"
        xml = fill_shape(
            tpl_xml,
            "标题 1",
            [(0, [(title, 0)])],
            sz=cover_title_size(title),
            typeface=COVER_TITLE_FACE,
        )
        # 版式标题框 25.4 cm 偏窄，长题目会把末词挤成孤行；加宽到 30.5 cm 仍居中。
        # 占位符原本无显式 xfrm，四个分量按 template-spec 的版式实测值写全。
        xml = set_shape_box(xml, "标题 1",
                            x=int(round(1.7 / CM_PER_EMU)), y=int(round(3.12 / CM_PER_EMU)),
                            cx=int(round(30.47 / CM_PER_EMU)), cy=int(round(6.63 / CM_PER_EMU)))
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
        # 三行：汇报人 / 单位 / 日期
        meta_specs = [
            (0, [("汇报人：" + presenter, 0)]),
            (0, [("单位：" + org, 0)]),
            (1, [(date, 0)]),
        ]
        xml = fill_shape_mixed(
            xml,
            "文本框 3",
            meta_specs,
            [COVER_META_PT, COVER_META_PT, COVER_META_PT],
            typeface=("楷体", "Times New Roman"),
        )
        xml = set_autofit(xml, "文本框 3", "noAutofit")
        return xml

    if kind == "closing":
        # 要点回顾页：Take-away（可 {{红粗}}）+ 出处一行；底部固定「感谢聆听」居中
        xml = tpl_xml
        title = title or "要点回顾"
        xml = fill_shape(xml, "标题 1", [(0, [(title, 0)])])
        summary = page.get("summary")
        info = page.get("info") or ""
        if summary or info:
            specs = []
            sizes = []
            if summary:
                specs.append((0, rich_runs(summary, 0)))
                sizes.append(24)
            if info:
                specs.append((1, rich_runs(info, 0)))
                sizes.append(15)
            xml = fill_shape_mixed(xml, "文本框 6", specs, sizes, typeface=BODY_FACE)
        else:
            xml = remove_shape(xml, "文本框 6")
        thanks = (page.get("thanks") or "感谢聆听").strip()
        if thanks:
            # 模板原样式：华文中宋 36 磅；这里只确保居中
            xml = fill_shape(xml, "文本框 3", [(0, [(thanks, 0)])])
            xml = force_align(xml, "文本框 3", "ctr")
        return xml

    if kind == "refs":
        # 参考文献页：与 PPT 内引用编号一一对应。复用 text-left 蓝图，
        # 删掉图槽占位、清空总结槽，正文框拉通全宽，条目字号 12–14 磅自适应。
        title = title or "参考文献"
        xml = fill_shape(tpl_xml, SLOT_TITLE, [(0, [(title, 0)])], sz=page_title_size(title))
        xml = remove_shape(xml, "矩形 2")
        xml = clear_shape(xml, SLOT_SUMMARY)
        xml = set_shape_box(xml, SLOT_BODY,
                            x=int(round(1.13 / CM_PER_EMU)), y=int(round(4.13 / CM_PER_EMU)),
                            cx=int(round(31.57 / CM_PER_EMU)), cy=int(round(12.40 / CM_PER_EMU)))
        entries = [str(e).strip() for e in (page.get("refs") or []) if str(e).strip()]
        page["_refs_n"] = len(entries)
        if entries:
            pt = fit_font_pt(entries, 31.0, 12.2, 14, 12)
            page["_refs_pt"] = pt
            body_block = find_shape(xml, SLOT_BODY) or ""
            paras = shape_paragraphs(body_block)
            tpl = paras[0] if paras else "<a:p><a:endParaRPr/></a:p>"
            new = "".join(build_paragraph(tpl, [(e, 0)], sz=pt * 100, typeface=BODY_FACE)
                          for e in entries)
            body = TXBODY_RE.search(body_block).group(0)
            open_tag = body[: body.index("<a:p>")] if "<a:p>" in body else body[: -len("</p:txBody>")]
            xml = xml.replace(body_block, body_block.replace(body, open_tag + new + "</p:txBody>"))
        else:
            xml = clear_shape(xml, SLOT_BODY)
        xml = set_autofit(xml, SLOT_BODY, "noAutofit")
        return xml

    # 正文页：左文右图 / 现状（左文右两图） / 左图右文
    xml = fill_shape(tpl_xml, SLOT_TITLE, [(0, [(title, 0)])], sz=page_title_size(title))

    # 图放进模板给这一页留的矩形里，一个矩形一张图，不切分、不重叠。
    # 没画出来的矩形删掉，避免模板里的「图1」「图2」占位文字留在成品里。
    slot_names = KIND_SLOTS.get(kind, {}).get("figures", [SLOT_FIGURE])
    canvases = page.get("_figure_shapes") or []
    pics = page.get("_figure_pics") or []
    # 模板留了多个图槽但这一页只画一张时，把这张图铺满这些槽的外接矩形，
    # 不要缩在其中一块里、旁边留着空白。
    if len(canvases) + len(pics) == 1 and len(slot_names) > 1:
        boxes = [shape_xfrm(find_shape(xml, name)) for name in slot_names]
        boxes = [b for b in boxes if b]
        if boxes:
            x0 = min(b["x"] for b in boxes); y0 = min(b["y"] for b in boxes)
            x1 = max(b["x"] + b["cx"] for b in boxes); y1 = max(b["y"] + b["cy"] for b in boxes)
            union = {"x": x0, "y": y0, "cx": x1 - x0, "cy": y1 - y0}
            for name in slot_names:
                block = find_shape(xml, name)
                if block:
                    xml = xml.replace(block, "")
            if canvases and _shapes is not None:
                # 画布是按原槽尺寸排的，铺满前按合并后的尺寸重画一次
                figs = normalize_figures(page.get("figure"))
                spec = _shapes.normalize_figure(figs[0]) if figs else None
                if spec:
                    spec = dict(spec)
                    spec["_w"] = union["cx"] * CM_PER_EMU
                    spec["_h"] = union["cy"] * CM_PER_EMU
                    redrawn = _shapes.render_shapes(spec)
                    canvas = redrawn or canvases[0]
                else:
                    canvas = canvases[0]
                # to_xml 的偏移单位是厘米，union 是 EMU
                drawn = _shapes.to_xml(canvas, union["x"] * CM_PER_EMU, union["y"] * CM_PER_EMU,
                                       image_rids=page.get("_image_rids"))
                xml = xml.replace("</p:spTree>", drawn + "</p:spTree>", 1)
            elif pics:
                rid, descr = pics[0][0], pics[0][1]
                drawn = pic_xml(next_cNvPr_id(xml), "图表", rid,
                                _fit_pic_xfrm(page, 0, union), descr=descr)
                xml = xml.replace("</p:spTree>", drawn + "</p:spTree>", 1)
            canvases, pics = [], []
    elif len(canvases) + len(pics) > 1 and len(slot_names) == 1 and _shapes is not None:
        # 镜像情形：模板只有一个图槽但这一页给了 2~3 组图（装配阶段已把 _slots
        # 上下切分好），模板槽矩形删掉，每组图按 figure 数组顺序放进对应子格。
        boxes = page.get("_slots") or []
        ordered = ordered_figure_slots(page)
        block = find_shape(xml, slot_names[0])
        if block is not None:
            xml = xml.replace(block, "")
        for n, (fig_kind, k) in enumerate(ordered[:len(boxes)]):
            box = boxes[n]
            emu_box = {"x": int(round(box["x"] / CM_PER_EMU)),
                       "y": int(round(box["y"] / CM_PER_EMU)),
                       "cx": int(round(box["cx"] / CM_PER_EMU)),
                       "cy": int(round(box["cy"] / CM_PER_EMU))}
            if fig_kind == "canvas":
                by_canvas = page.get("_image_rids_by_canvas") or []
                rids = by_canvas[k] if k < len(by_canvas) else page.get("_image_rids")
                drawn = _shapes.to_xml(canvases[k], box["x"], box["y"], image_rids=rids)
            else:
                rid, descr = pics[k][0], pics[k][1]
                drawn = pic_xml(next_cNvPr_id(xml), "图表", rid,
                                _fit_pic_xfrm(page, k, emu_box), descr=descr)
            xml = xml.replace("</p:spTree>", drawn + "</p:spTree>", 1)
        canvases, pics = [], []
    ordered = ordered_figure_slots(page)
    for n, name in enumerate(slot_names):
        block = find_shape(xml, name)
        xfrm = shape_xfrm(block) if block is not None else None
        if xfrm is None:
            continue
        # 优先用装配阶段算好的槽几何（单槽切分页与模板矩形一致或为其半格）
        slots_geo = page.get("_slots") or []
        if n < len(slots_geo):
            box = slots_geo[n]
        else:
            box = {"x": xfrm["x"] * CM_PER_EMU, "y": xfrm["y"] * CM_PER_EMU,
                   "cx": xfrm["cx"] * CM_PER_EMU, "cy": xfrm["cy"] * CM_PER_EMU}
        if n < len(ordered) and _shapes is not None:
            fig_kind, k = ordered[n]
            if fig_kind == "canvas":
                by_canvas = page.get("_image_rids_by_canvas") or []
                rids = by_canvas[k] if k < len(by_canvas) else page.get("_image_rids")
                xml = xml.replace(block, _shapes.to_xml(
                    canvases[k], box["x"], box["y"], image_rids=rids))
            else:
                rid, descr = pics[k][0], pics[k][1]
                emu_box = {"x": int(round(box["x"] / CM_PER_EMU)), "y": int(round(box["y"] / CM_PER_EMU)),
                           "cx": int(round(box["cx"] / CM_PER_EMU)), "cy": int(round(box["cy"] / CM_PER_EMU))}
                pic_xfrm = _fit_pic_xfrm(page, k, emu_box)
                xml = xml.replace(block, pic_xml(next_cNvPr_id(xml), "图表", rid, pic_xfrm, descr=descr))
        else:
            # 这一张画不出：矩形留着，占位文字换成「待补充」，不并进别的图
            xml = fill_shape(xml, name, [(0, [("待补充", 0)])], sz=1800, typeface=BODY_FACE)

    # 正文：一级标题楷体 18 磅、二级楷体 16 磅，缩进用模板段落自带的，不在这里重算。
    slots = KIND_SLOTS.get(kind, {})
    body_name = slots.get("body", SLOT_BODY)

    # 正文框与图槽对齐：上沿抬到图槽上沿、下沿收到总结框上方，左右贴回图边。
    # 模板蓝图里的正文框是原始位置，这里按 kind 的实测几何统一调整。
    body_block = find_shape(xml, body_name)
    if body_block is not None:
        bx = shape_xfrm(body_block)
        if bx:
            aligned = align_body_and_figure(bx, kind)
            xml = set_shape_box(xml, body_name, x=aligned["x"], y=aligned["y"],
                                cx=aligned["cx"], cy=aligned["cy"])

    points = [normalize_point(pt) for pt in (page.get("points") or [])]
    page["_body_pt"] = LEVEL1_PT
    body_paras = shape_paragraphs(find_shape(xml, body_name) or "")
    lvl1 = next((i for i, para in enumerate(body_paras) if 'lvl="' not in para and "<a:r>" in para), 0)
    lvl2_idx = next((i for i, para in enumerate(body_paras) if 'lvl="1"' in para), None)
    # 模板这一页没有二级段落时，就复用一级段落模板（pPr 由 bullet 参数整体重写）
    lvl2 = lvl2_idx if lvl2_idx is not None else lvl1

    # 正文格式（check_ppt 与 template-spec 同步）：一级 ▶ 18 磅、文字 1.0 cm 起；
    # 二级 ● 16 磅、文字 = 一级文字 + 2 个 16 磅字宽（约 2.13 cm）。
    lvl1_bullet = {"char": BULLET_L1, "marL_cm": 1.0, "hang_cm": 1.0}
    lvl2_bullet = {"char": BULLET_L2, "marL_cm": 2.13, "hang_cm": 1.13}

    filled = []
    for item in points:
        # 标题式要点：标签（含冒号，带下划线）+ 短主句（{{红粗}}/**粗体** 可用，无下划线）
        if item["label"] is None:
            runs = rich_runs(item["text"], 0)
        else:
            label_run = (item["label"] + "：", 0, {"keep_u": True})
            runs = [label_run] + rich_runs(item["text"], 1)
        filled.append((lvl1, runs, LEVEL1_PT, lvl1_bullet))
        for sub in item["subs"]:
            filled.append((lvl2, rich_runs(sub, 0), LEVEL2_PT, lvl2_bullet))
    if filled:
        xml = fill_body(xml, filled, body_name, BODY_FACE)
        xml = set_autofit(xml, body_name, "noAutofit")
    else:
        xml = clear_shape(xml, body_name)

    summary = page.get("summary")
    summary_name = slots.get("summary", SLOT_SUMMARY)
    if summary and summary_name:
        sum_block = find_shape(xml, summary_name)
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
        xml = fill_shape(xml, summary_name, [(0, [(summary, 0)])], sz=sum_pt * 100, typeface=BODY_FACE)
        xml = set_autofit(xml, summary_name, "noAutofit")
    elif summary_name:
        xml = clear_shape(xml, summary_name)
    return xml


def align_body_and_figure(body_xfrm, kind):
    """正文框与图表槽对齐到同一条上沿、同一条下沿。

    模板里正文框比图表槽更靠下、更矮（text-left 差约 2 cm，figure-left 差约 2.5 cm），
    直接填充会一边高一边矮。这里把正文上沿抬到图表槽上沿，下沿收到总结框上方，
    两边视觉高度一致；左右位置按 FIG_GAP_CM 贴回图边。
    """
    fig_top = FIGURE_TOP.get(kind, body_xfrm["y"] * CM_PER_EMU)
    fig_bottom = FIGURE_BOTTOM.get(kind, fig_top + 9.03)
    summary_top = SUMMARY_TOP.get(kind)
    # 下沿收到总结框上方 0.3 cm；无总结槽（status）时收到图槽下沿。
    # 概述/现状这类文字密的页需要比图槽更高的正文框。
    bottom = (summary_top - 0.3) if summary_top else fig_bottom
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


def fill_body(xml, filled, body_name=None, typeface=None):
    """filled: [(模板段落序号, runs, 磅, bullet 字典或 None), ...]"""
    body_name = body_name or SLOT_BODY
    block = find_shape(xml, body_name)
    if block is None:
        raise KeyError("模板中找不到形状：%s" % body_name)
    paras = shape_paragraphs(block)
    if not paras:
        raise KeyError("形状 %s 没有段落模板" % body_name)
    new = "".join(
        build_paragraph(paras[min(ptpl, len(paras) - 1)], runs, sz * 100, typeface, bullet)
        for ptpl, runs, sz, bullet in filled
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
        page["_index"] = i

        # 图的尺寸取模板蓝图里各个矩形的真实尺寸，画布按这个尺寸排。
        # 单图槽的版式（text-left / figure-left）给了 2~3 组图时，把槽上下切分，
        # 一组一格——稀疏页（图小字少）靠这个补第二张图，页面才饱满。
        slots = []
        tpl_xfrm = None
        for name in KIND_SLOTS.get(kind, {}).get("figures", []):
            block = find_shape(tpl_slides[bp], name)
            xfrm = shape_xfrm(block) if block is not None else None
            if xfrm:
                tpl_xfrm = xfrm
                slots.append({"x": xfrm["x"] * CM_PER_EMU, "y": xfrm["y"] * CM_PER_EMU,
                              "cx": xfrm["cx"] * CM_PER_EMU, "cy": xfrm["cy"] * CM_PER_EMU})
        fig_in = page.get("figure")
        n_figs = len(fig_in) if isinstance(fig_in, list) else (1 if fig_in else 0)
        if len(slots) == 1 and tpl_xfrm is not None and n_figs >= 2:
            slots = split_slot(tpl_xfrm, min(n_figs, 3))
        page["_slots"] = slots
        prepared = prepare_figure(page, i, media_dir)
        page["_figure_missing"] = prepared["missing"]
        media_seq = []

        def add_media(path):
            media_seq.append(path)
            name = "generated_slide%d_%d.png" % (i, len(media_seq))
            generated_media["ppt/media/" + name] = path
            return "rIdImg%d" % len(media_seq), name

        for canvas, slot in zip(prepared["shapes"], page.get("_slots") or []):
            if canvas.h > slot["cy"] + 0.05 or canvas.w > slot["cx"] + 0.05:
                page.setdefault("_figure_missing", []).append(
                    "图高 %.1fcm 超过子槽 %.1fcm，换更矮的图或减少层数" % (canvas.h, slot["cy"]))
        # rid 映射按画布分开存：to_xml 里的图片下标是「这一个画布内」从 0 数的，
        # 一页有多组图时用整页累计下标会让后面的画布取到前面画布的图。
        image_rids_by_canvas = []
        for canvas in prepared["shapes"]:
            rids = {}
            for img_i, path in enumerate(_shapes.canvas_images(canvas)):
                rid, _name = add_media(path)
                rids[img_i] = rid
            image_rids_by_canvas.append(rids)
        page["_image_rids_by_canvas"] = image_rids_by_canvas
        page["_image_rids"] = image_rids_by_canvas[0] if image_rids_by_canvas else {}
        page["_figure_shapes"] = prepared["shapes"]
        page["_figure_pics"] = []
        for path, descr in prepared["images"]:
            rid, _name = add_media(path)
            page["_figure_pics"].append((rid, descr, path))

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
        while ("rId%d" % nid) in used:
            nid += 1
        items.append(("rId%d" % nid, "notesSlide", "../notesSlides/notesSlide%d.xml" % i))
        for seq, _path in enumerate(media_seq, start=1):
            items.append(("rIdImg%d" % seq, "image", "../media/generated_slide%d_%d.png" % (i, seq)))
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
        write_narration(pages, narr, narration_out, doc_title=content.get("title"))
    return report(pages, narr, out_path)


def prepare_figure(page, index, media_dir):
    """把 page.figure 变成可插入的东西。

    figure 可以是一个对象，也可以是最多 MAX_FIGURES 个对象。
    返回 {"shapes": [canvas, ...], "images": [(png路径, 图题), ...], "missing": [说明, ...]}。
    自己画的图走形状；type=image 的原图走位图；logos 里打不开的路径记进 missing。
    """
    result = {"shapes": [], "images": [], "missing": []}
    slots = page.get("_slots") or []
    limit = len(slots) or None
    figures = normalize_figures(page.get("figure"), limit)
    extra = page.get("figure")
    if isinstance(extra, list) and limit and len(extra) > limit:
        result["missing"].append("给出 %d 组图，这一页模板只有 %d 个图槽，多出的未画" % (len(extra), limit))
    if not figures or _shapes is None:
        return result
    os.makedirs(media_dir, exist_ok=True)
    for n, fig in enumerate(figures, start=1):
        spec = fig
        if isinstance(fig, str):
            spec = {"type": "image", "path": fig} if os.path.exists(fig) else None
        if spec is None:
            result["missing"].append("第 %d 组图无法识别" % n)
            continue
        spec = _shapes.normalize_figure(spec)
        if spec is None:
            result["missing"].append("第 %d 组图类型不受支持" % n)
            continue
        slot = (page.get("_slots") or [None])[n - 1] if page.get("_slots") else None
        if slot:
            spec = dict(spec)
            spec["_w"] = slot["cx"]
            spec["_h"] = slot["cy"]
        if spec.get("type") == "image":
            dest = os.path.join(media_dir, "slide%d_%d.png" % (index, n))
            try:
                path = _shapes.render_image(spec, dest)
            except Exception as exc:
                result["missing"].append("第 %d 组原图嵌入失败：%s" % (n, exc))
                continue
            if path:
                result["images"].append((path, spec.get("caption") or spec.get("title") or ""))
            else:
                result["missing"].append("第 %d 组原图打不开：%s" % (n, spec.get("path") or ""))
            continue
        canvas = _shapes.render_shapes(spec)
        if canvas is None or not canvas.count():
            result["missing"].append("第 %d 组图没有画出形状" % n)
            continue
        result["shapes"].append(canvas)
        if spec.get("type") == "logos":
            wanted = spec.get("items") or []
            have = set(_shapes.canvas_images(canvas))
            for it in wanted:
                path = it if isinstance(it, str) else (it.get("path") or it.get("image") or "")
                if path and path not in have:
                    result["missing"].append("logo 打不开，已跳过：%s" % path)
    return result


def write_narration(pages, narr, path, doc_title=None):
    """逐页旁白稿 md。格式与 build_docx.py 的方言一一对应：
    `# ` 文档标题、`## ` 各页、正文段以两个全角空格开头，可直接转 docx。"""
    total = 0
    lines = ["# %s——论文总结PPT旁白稿" % (doc_title or "论文"),
             "",
             "> 共 %d 页（含封面与结束页）；按每分钟 200 字折算，须在 15 分钟内讲完。" % len(pages),
             ""]
    for i, page in enumerate(pages, start=1):
        text = narr(i)
        total += len(text)
        if page.get("kind") == "cover":
            head = "封面"
        elif page.get("kind") == "closing":
            head = page.get("title") or "结束页"
        else:
            head = page.get("title") or ""
        lines.append("## 第%d页 %s" % (i, head))
        lines.append("")
        for para in (text or "（未提供）").split("\n"):
            para = para.strip()
            if para:
                lines.append("\u3000\u3000" + para)
            else:
                lines.append("")
        lines.append("")
    lines.append("**旁白总字数：%d 字（约 %.1f 分钟）**" % (total, total / 200.0))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# 论文总结固定八节：标题前缀必须按此顺序出现（同节可多页）
SECTION_ORDER = ["一.论文概述", "二.研究背景", "三.研究问题", "四.传统研究现状",
                 "五.本文研究方法", "六.示例说明", "七.实验结果", "八.总结"]
# 各节最少页数：方法要有原理页 + 步骤页，示例说明至少 1 页，实验要主结果 + 分析
SECTION_MIN_PAGES = {"五.本文研究方法": 2, "六.示例说明": 1, "七.实验结果": 2}
MIN_PAGES, MAX_PAGES = 13, 17
REFS_MAX_ENTRIES = 10   # 参考文献页最多 10 条（只列 PPT 里引用过的）


def check_content_rules(index, page):
    """把写进 content-spec 的硬规则变成脚本检查，别只停在文档里。"""
    out = []
    kind = page.get("kind")
    title = page.get("title") or ""
    points = page.get("points") or []
    if kind == "cover":
        if not title.strip():
            out.append("第%d页：封面缺论文题目（中文译名，可括注英文原题）" % index)
        if not (page.get("presenter") or "").strip():
            out.append("第%d页：封面缺 `presenter`，右下角会只剩一个「汇报人：」" % index)
        if not (page.get("date") or "").strip():
            out.append("第%d页：封面缺 `date`，右下角日期会空着" % index)
        sub = (page.get("subtitle") or "").strip()
        if not sub:
            out.append("第%d页：封面缺副标题，应写「发表于 会议/期刊 年份（级别）· 论文总结汇报」" % index)
        elif not re.search(r"(19|20)\d{2}", sub):
            out.append("第%d页：封面副标题缺年份：%s" % (index, sub[:30]))
        return out
    if kind == "refs":
        entries = [str(e).strip() for e in (page.get("refs") or []) if str(e).strip()]
        if not entries:
            out.append("第%d页：参考文献页没有条目（refs 数组为空）" % index)
        for e in entries:
            if not re.match(r"^\[\d+\]", e):
                out.append("第%d页：参考文献条目须以 [N] 开头与引用对应：%s" % (index, e[:24]))
        if len(entries) > REFS_MAX_ENTRIES:
            out.append("第%d页：参考文献 %d 条，超过 %d 条上限（只列 PPT 里引用过的）"
                       % (index, len(entries), REFS_MAX_ENTRIES))
        return out
    if kind == "closing":
        if not (page.get("summary") or "").strip():
            out.append("第%d页：结束页缺一句话点题（论文题目原文 + 出处）" % index)
        return out
    # 内容页通用规则
    if not points:
        out.append("第%d页：正文没有要点，图文要都有" % index)
    if len(points) > MAX_POINTS:
        out.append("第%d页：要点 %d 条，超过 %d 条上限" % (index, len(points), MAX_POINTS))
    if KIND_SLOTS.get(kind, {}).get("summary") and not (page.get("summary") or "").strip():
        out.append("第%d页：缺底部总结句（本页一句话结论）" % index)
    summary = page.get("summary") or ""
    if summary.startswith("针对"):
        out.append("第%d页：总结句套了「针对……」。总结句说这一页的结论是什么，"
                   "当前是：%s" % (index, summary[:24]))
    # 现状页：每类方法要带原文引用
    if title.startswith("四."):
        body = " ".join(point_text(pt) for pt in points)
        if not re.search(r"\[\d+\]|et al\.|等人|〔\d+〕|［\d+］", body):
            out.append("第%d页：现状页要点没有原文引用标记（如 [12]、AttAcc [2]），"
                       "每类方法都要落到具体文献" % index)
    # 示例说明页：用小例子贯穿方法，逐步推演到结果（关键项）
    if title.startswith("六."):
        body = " ".join(point_text(pt) for pt in points) + (page.get("summary") or "")
        if "示例" not in body and "例子" not in body:
            out.append("第%d页：示例说明页要点没有出现「示例/例子」——这页是用小例子贯穿方法" % index)
    # 总结页：必须提炼不足与启发（关键项）
    if title.startswith("八."):
        body = " ".join(point_text(pt) for pt in points) + summary
        for word, hint in (("不足", "本文的不足/局限"), ("启发", "对后续研究或自己工作的启发")):
            if word not in body and not any(word in s for pt in points for s in normalize_point(pt)["subs"]):
                out.append("第%d页：总结页没有提到「%s」（%s），这是硬性要求" % (index, word, hint))
    return out


def figure_types(page):
    figs = normalize_figures(page.get("figure"))
    out = []
    for fig in figs:
        if isinstance(fig, str):
            out.append("image")
        elif isinstance(fig, dict):
            out.append(fig.get("type") or "")
    return out


def check_figure_fit(index, page):
    """图表选型自检：图要对得上这一页，而且不能是换个壳的文字罗列。"""
    out = []
    kind = page.get("kind")
    figs = normalize_figures(page.get("figure"))
    raw = page.get("figure")
    slot_n = len(KIND_SLOTS.get(kind, {}).get("figures", []))
    # 单图槽版式给 2~3 组图会自动上下切分（split_slot），按切分后的容量算
    capacity = slot_n if slot_n > 1 else 3
    if isinstance(raw, list) and capacity and len(raw) > capacity:
        out.append("第%d页：图给了 %d 组，这一页版式最多放 %d 组" % (index, len(raw), capacity))
    if not figs:
        if kind in KIND_SLOTS:
            out.append("第%d页：没有图。论文总结每个内容页都要图文都有（原文图或自绘）" % index)
        return out
    types = figure_types(page)
    title = page.get("title") or ""
    # 现状页要「分类框架 + 对比表」双图：框架可自绘，对比表优先原文 Table
    if title.startswith("四."):
        if slot_n >= 2 and len(figs) < 2:
            out.append("第%d页：现状页有 %d 个图槽，建议分类框架图 + 对比表各占一槽，当前只给了 %d 组图"
                       % (index, slot_n, len(figs)))
    if kind in ("text-left", "figure-left", "status") and not (page.get("points") or page.get("heading")):
        out.append("第%d页：只有图没有文字要点，图文要都有" % index)
    for msg in page.get("_figure_missing") or []:
        out.append("第%d页：%s" % (index, msg))
    return out


def check_structure(pages):
    """整份 PPT 的章节结构：八节齐全有序、方法与实验至少 2 页、参考文献页在总结后、页数范围。"""
    out = []
    if not pages or pages[0].get("kind") != "cover":
        out.append("第 1 页必须是封面（kind=cover）")
    if not pages or pages[-1].get("kind") != "closing":
        out.append("最后一页必须是结束页（kind=closing）")
    n_refs = sum(1 for p in pages if p.get("kind") == "refs")
    if n_refs == 0:
        out.append("缺少参考文献页（kind=refs，放在八.总结之后、结束页之前）")
    elif n_refs > 1:
        out.append("参考文献页只能有 1 页，当前 %d 页" % n_refs)
    elif len(pages) < 2 or pages[-2].get("kind") != "refs":
        out.append("参考文献页应紧邻结束页之前（倒数第二页），当前不在该位置")
    titles = [p.get("title") or "" for p in pages[1:-1] if p.get("kind") in KIND_SLOTS]
    if len(pages) < MIN_PAGES or len(pages) > MAX_PAGES:
        out.append("总页数 %d，应在 %d–%d 页（15 分钟讲不完更多页）" % (len(pages), MIN_PAGES, MAX_PAGES))
    # 章节前缀按序出现；允许一个标题只覆盖一节，同节连续多页
    seq = []
    for t in titles:
        for sec in SECTION_ORDER:
            if t.startswith(sec):
                seq.append(sec)
                break
        else:
            out.append("页标题「%s」没有以七节之一开头（如「五.本文研究方法：核心原理」）" % t[:24])
    if seq != sorted(seq, key=SECTION_ORDER.index) or len(set(seq)) != len(seq):
        # 逐段判断是否按 SECTION_ORDER 递增
        order_idx = [SECTION_ORDER.index(s) for s in seq]
        if order_idx != sorted(order_idx):
            out.append("章节顺序错乱：%s" % " → ".join(seq))
    for sec in SECTION_ORDER:
        n = seq.count(sec)
        if n == 0:
            out.append("缺少章节「%s」" % sec)
        elif n < SECTION_MIN_PAGES.get(sec, 1):
            out.append("章节「%s」只有 %d 页，至少 %d 页" % (sec, n, SECTION_MIN_PAGES[sec]))
    return out


def report(pages, narr, out_path):
    print("输出文件：%s" % out_path)
    print("总页数：%d" % len(pages))
    warn = check_structure(pages)
    for i, page in enumerate(pages, start=1):
        title = page.get("title") or page.get("kind")
        points = page.get("points") or []
        n_chars = sum(len(point_text(p)) for p in points)
        line = "  第%2d页 [%-12s] %s" % (i, page.get("kind"), title)
        line += "  要点%d条/%d字" % (len(points), n_chars)
        if page.get("kind") == "refs":
            line += "  文献%d条/%d磅" % (page.get("_refs_n", 0), page.get("_refs_pt", 0))
        if page.get("_body_pt"):
            line += "  正文%d磅" % page["_body_pt"]
        if page.get("summary"):
            line += "  总结%d字" % len(page["summary"])
            if page.get("_summary_pt"):
                line += "/%d磅" % page["_summary_pt"]
        canvases = page.get("_figure_shapes") or []
        if canvases:
            line += "  形状图%d组/%d个（可编辑）" % (len(canvases), sum(c.count() for c in canvases))
        elif page.get("_figure_png") or page.get("_figure_pics"):
            n = len(page.get("_figure_pics") or []) or 1
            line += "  原图%d张" % n
        nar = narr(i)
        if nar:
            line += "  旁白%d字" % len(nar)
        print(line)
        if len(points) > MAX_POINTS:
            warn.append("第%d页：要点 %d 条，超过 %d 条上限" % (i, len(points), MAX_POINTS))
        pt_limit = point_char_limit(page)
        for p in points:
            item = normalize_point(p)
            main = (item["label"] + "：" + item["text"]) if item["label"] else item["text"]
            disp = disp_width(main)
            if disp > pt_limit:
                warn.append("第%d页：要点「%s」%.0f 字宽，超过 %d 字建议上限，请精简（正文固定 18/16 磅，超字会挤占框高）" % (i, main[:12], disp, pt_limit))
            for sub in item["subs"]:
                if disp_width(sub) > SUB_CHARS:
                    warn.append("第%d页：二级行「%s」%.0f 字宽，超过 %d 字，请拆短" % (i, strip_emph(sub)[:12], disp_width(sub), SUB_CHARS))
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
        kind = page.get("kind")
        if kind in KIND_SLOTS:
            body_all = " ".join(
                [strip_emph(point_text(pt)) for pt in points]
                + [strip_emph(s) for pt in points for s in normalize_point(pt)["subs"]])
            if "。" in body_all:
                warn.append("第%d页：正文里出现句号——要点要写成标题式短语，句子放旁白里讲" % i)
            raw_all = " ".join([point_text(pt) for pt in points]
                               + [s for pt in points for s in normalize_point(pt)["subs"]])
            if "{{" not in raw_all:
                warn.append("第%d页：没有 {{红粗}} 标记——关键数字/方法名/结论词要标红加粗" % i)
        for canvas in page.get("_figure_shapes") or []:
            for name, text, need, have in _shapes.text_overflows(canvas):
                warn.append("第%d页：图内文字被截断「%s」（需要 %.2fcm，框高 %.2fcm），放大文本框或缩短后再生成"
                            % (i, text[:18], need, have))
            # 图内遮挡与出框：图里两个框压在一起，比字小一号严重得多
            for a, b, ow, oh in _shapes.shape_overlaps(canvas):
                warn.append("第%d页：图内「%s」与「%s」重叠 %.2f×%.2fcm，减少节点或加大间距"
                            % (i, a, b, ow, oh))
            for name, x0, y0, x1, y1 in _shapes.out_of_canvas(canvas):
                warn.append("第%d页：图内「%s」超出图槽（%.2f, %.2f）–（%.2f, %.2f）"
                            % (i, name, x0, y0, x1, y1))
            for it in canvas.items:
                if it.get("kind") == "arrow":
                    span = abs(it["x2"] - it["x1"]) + abs(it["y2"] - it["y1"])
                    if span < 0.6:
                        warn.append("第%d页：箭头只有 %.2fcm，节点之间空隙不够，加大到 0.7cm 以上" % (i, span))
        # 图标题与页标题重复：页面标题已经说了这件事，图里再写一遍是白占一行
        page_title = (page.get("title") or "").strip()
        for fig in normalize_figures(page.get("figure")):
            if not isinstance(fig, dict):
                continue
            ft = (fig.get("title") or "").strip()
            if ft and page_title and (ft == page_title or ft in page_title):
                warn.append("第%d页：图标题「%s」与页标题重复，删掉图标题把高度让给图" % (i, ft))
        fig = page.get("figure")
        has_fig = page.get("_figure_png") or page.get("_figure_shapes") or page.get("_figure_pics")
        if fig and fig != FIG_PLACEHOLDER and not has_fig:
            if isinstance(fig, dict) or (isinstance(fig, str) and os.path.exists(fig)):
                warn.append("第%d页：图表未成功嵌入，槽位退回文字说明" % i)
        if has_fig and "图" not in (narr(i) or ""):
            warn.append("第%d页：有图但旁白没有提到图——每张图都要在旁白里解释（是什么/怎么看/说明什么）" % i)
    total = sum(len(narr(i)) for i in range(1, len(pages) + 1))
    if total:
        print("旁白总字数：%d 字（目标 %d–%d 字，约 %.1f 分钟）" % (total, NARRATION_MIN, NARRATION_MAX, total / 200.0))
        if not (NARRATION_MIN <= total <= NARRATION_MAX):
            warn.append("旁白总字数 %d 字，超出 %d–%d 字" % (total, NARRATION_MIN, NARRATION_MAX))
        spoken = ("卡住", "对症", "说白了", "所以呢", "说白", "咱们", "呃", "嗯呢")
        for i in range(1, len(pages) + 1):
            text = narr(i)
            hit = [w for w in spoken if w in text]
            if hit:
                warn.append("第%d页旁白有口语「%s」。这是给评审专家的，按 narration-spec 的框架重写" % (i, "、".join(hit)))
    else:
        warn.append("未提供旁白，备注页为空")
    for w in warn:
        print("  [校验] %s" % w)
    return warn


def main():
    ap = argparse.ArgumentParser(description="把逐页内容填进论文总结模板，生成 PPT（旁白写入备注页）")
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
