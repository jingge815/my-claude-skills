#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 figure 对象渲染成 PPT 原生形状（可编辑），而不是位图。

交付的 PPT 里，自己画的图必须能点开、能改字、能挪框、能换色。位图做不到这一点，
所以流程 / 分层框架 / 逻辑链 / 卡片 / 对比 / 表格 / 柱状 / 饼图 / 甘特都直接生成
OOXML 形状，插进模板图表槽的位置。申请书原图（`type: image`）仍按位图嵌入——
它就是一张现成的图，不需要编辑。

坐标一律用厘米，槽位是 17.06 × 9.03；序列化时换成 EMU 并加上槽位偏移。
"""

from __future__ import print_function

import math

# 与模板/正文一致的字体与配色
FONT = "楷体"
INK = "202830"
WHITE = "FFFFFF"
MUTED = "6E7681"
GRID = "DCDFE3"
DK2 = "44546A"
SOFT = "F1F6FB"        # 浅底
SOFT_ALT = "F7F7F7"
SERIES = ["5B9BD5", "ED7D31", "70AD47", "FFC000", "4472C4", "A5A5A5"]

SLOT_W = 17.06
SLOT_H = 9.03

# 位图路径（原图嵌入、自查预览）要的是字体**文件**；形状里的文字只用字体名 FONT。
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/kaiti/KaiTi.ttf",
    "/usr/share/fonts/truetype/kaiti/kaiti.ttf",
    "/usr/share/fonts/truetype/arphic/ukai.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/Supplemental/Kaiti.ttc",
    "C:/Windows/Fonts/simkai.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

# 形状里的文字：目标是 16–18 磅，实在挤不下才降到 14
PT_MAX = 18
PT_MIN = 14
EMU_PER_CM = 360000
PAD = 0.18
GAP = 0.30


def _emu(v):
    return int(round(v * EMU_PER_CM))


def _esc(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _disp(text):
    """显示宽度：CJK 记 1，ASCII 记 0.55。"""
    return sum(0.55 if ord(c) < 128 else 1.0 for c in (text or ""))


def _n_lines(text, box_w_cm, pt):
    usable = max(0.8, box_w_cm - 0.16)
    cpl = max(2.0, usable / (0.56 * pt / 16.0))
    return max(1, int((_disp(text) + cpl - 0.01) / cpl))


def text_height(texts, box_w_cm, pt):
    """一组文字按给定字号折行后需要的高度（厘米）。"""
    lines = sum(_n_lines(t, box_w_cm, pt) for t in texts)
    return lines * (pt * 1.30 / 28.35)


def fit_pt(texts, box_w_cm, box_h_cm, hi=PT_MAX, lo=PT_MIN):
    """挑一个能放下的字号：从大到小试，行数 × 行高 不超过框高。"""
    if hi < lo:
        hi = lo
    for pt in range(hi, lo - 1, -1):
        if text_height(texts, box_w_cm, pt) <= box_h_cm:
            return pt
    return lo


def fit_box(texts, box_w_cm, box_h_cm, hi=PT_MAX, lo=PT_MIN):
    """文字放不下时先加高文本框，加到仍放不下再降字号。

    返回 (字号, 框高)。框高至少是传入的 box_h_cm，只增不减，
    调用方负责把加出来的高度让给相邻的框。
    """
    pt = hi if hi >= lo else lo
    need = text_height(texts, box_w_cm, pt)
    if need <= box_h_cm:
        return pt, box_h_cm
    # 18 磅放不下就降字号，但框至少给到这个字号实际需要的高度
    pt = fit_pt(texts, box_w_cm, max(box_h_cm, need), hi, lo)
    return pt, max(box_h_cm, text_height(texts, box_w_cm, pt))


class Canvas(object):
    """收集一张图里的形状，最后一次性序列化成 XML。"""

    def __init__(self, w=SLOT_W, h=SLOT_H):
        self.w = w
        self.h = h
        self.items = []
        self.overflows = []  # (形状名, 文字, 需要的高度, 实际框高)

    # ---- 基本图元 -------------------------------------------------
    def rect(
        self,
        x,
        y,
        w,
        h,
        fill=None,
        line=None,
        radius=0.06,
        texts=None,
        pt=PT_MAX,
        color=INK,
        bold=False,
        align="ctr",
        anchor="ctr",
        line_w=1.0,
        name="矩形",
    ):
        """texts: [(文本, 字号, 颜色, 加粗)]，每项一段。"""
        if texts and "标题" not in name and "名" not in name and "头" not in name:
            pt0 = texts[0][1] if len(texts[0]) > 1 else pt
            need = text_height([t[0] for t in texts], w, pt0)
            # 按「能放下几行」判，不按高度差加一行的余量判：
            # 余量写成一整行时，4 行文字塞进 3 行高的框算不出问题，而那一行是真被切掉的。
            line_h = pt0 * 1.30 / 28.35
            need_lines = max(1, int(round(need / line_h)))
            fit_lines = max(1, int((h + 0.04) / line_h))
            if need_lines > fit_lines:
                self.overflows.append((name, " / ".join(str(t[0]) for t in texts), round(need, 2), round(h, 2)))
        self.items.append(
            {
                "kind": "rect",
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "fill": fill,
                "line": line,
                "radius": radius,
                "texts": texts or [],
                "pt": pt,
                "color": color,
                "bold": bold,
                "align": align,
                "anchor": anchor,
                "line_w": line_w,
                "name": name,
            }
        )

    def text(self, x, y, w, h, texts, align="ctr", anchor="ctr", name="文本"):
        self.rect(x, y, w, h, None, None, 0, texts, align=align, anchor=anchor, name=name)

    def arrow(self, x1, y1, x2, y2, color="4472C4", width=1.6, name="箭头"):
        self.items.append(
            {"kind": "arrow", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
             "color": color, "width": width, "name": name}
        )

    def sector(self, cx, cy, r, a0, a1, color, name="扇区"):
        """饼图扇区。a0/a1 是角度（度），0 度在 3 点方向，顺时针为正。"""
        self.items.append(
            {"kind": "pie", "cx": cx, "cy": cy, "r": r, "a0": a0, "a1": a1,
             "color": color, "name": name}
        )

    def count(self):
        return len(self.items)


# ------------------------------------------------------------------
# 序列化
# ------------------------------------------------------------------
def _rpr(pt, color, bold):
    return (
        '<a:rPr lang="zh-CN" altLang="en-US" sz="%d" b="%d" dirty="0">'
        '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
        '<a:latin typeface="%s"/><a:ea typeface="%s"/><a:cs typeface="%s"/>'
        "</a:rPr>" % (int(round(pt * 100)), 1 if bold else 0, color, FONT, FONT, FONT)
    )


def _body(texts, align, anchor, extra=None):
    if not texts:
        return ""
    paras = []
    for item in texts:
        s = str(item[0])
        pt = item[1] if len(item) > 1 else PT_MAX
        color = item[2] if len(item) > 2 else INK
        bold = item[3] if len(item) > 3 else False
        paras.append(
            '<a:p><a:pPr algn="%s"><a:lnSpc><a:spcPct val="108000"/></a:lnSpc>'
            "<a:buNone/></a:pPr>"
            "<a:r>%s<a:t>%s</a:t></a:r>"
            '<a:endParaRPr lang="zh-CN" altLang="en-US" sz="%d">'
            '<a:solidFill><a:srgbClr val="%s"/></a:solidFill></a:endParaRPr>'
            "</a:p>" % (align, _rpr(pt, color, bold), _esc(s), int(round(pt * 100)), color)
        )
    return (
        '<p:txBody><a:bodyPr wrap="square" lIns="%d" rIns="%d" tIns="%d" bIns="%d"'
        ' anchor="%s"><a:noAutofit/></a:bodyPr><a:lstStyle/>%s</p:txBody>'
        % (_emu(0.06), _emu(0.06), _emu(0.03), _emu(0.03), anchor, "".join(paras))
    )


def _sp(item, sid, ox, oy):
    x, y = item["x"], item["y"]
    w, h = max(0.05, item["w"]), max(0.05, item["h"])
    fill = (
        "<a:noFill/>" if item["fill"] is None
        else '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % item["fill"]
    )
    if item["line"] is None:
        line = "<a:ln><a:noFill/></a:ln>"
    else:
        line = (
            '<a:ln w="%d"><a:solidFill><a:srgbClr val="%s"/></a:solidFill></a:ln>'
            % (int(round(item["line_w"] * 12700)), item["line"])
        )
    geom = "roundRect" if item["radius"] and item["radius"] > 0 else "rect"
    avlst = '<a:avLst><a:gd name="adj" fmla="val %d"/></a:avLst>' % int(
        max(0, min(50000, item["radius"] / max(min(w, h), 0.01) * 100000))
    )
    return (
        '<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        "<p:spPr><a:xfrm><a:off x=\"%d\" y=\"%d\"/><a:ext cx=\"%d\" cy=\"%d\"/></a:xfrm>"
        '<a:prstGeom prst="%s">%s</a:prstGeom>%s%s</p:spPr>%s</p:sp>'
        % (
            sid,
            _esc(item["name"]),
            _emu(ox + x),
            _emu(oy + y),
            _emu(w),
            _emu(h),
            geom,
            avlst,
            fill,
            line,
            _body(item["texts"], item["align"], item["anchor"]),
        )
    )


def _arrow(item, sid, ox, oy):
    x1, y1, x2, y2 = item["x1"], item["y1"], item["x2"], item["y2"]
    x, y = min(x1, x2), min(y1, y2)
    cx, cy = abs(x2 - x1), abs(y2 - y1)
    flipH = ' flipH="1"' if x2 < x1 else ""
    flipV = ' flipV="1"' if y2 < y1 else ""
    return (
        '<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="%d" name="%s"/><p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr>'
        '<p:spPr><a:xfrm%s%s><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="straightConnector1"><a:avLst/></a:prstGeom>'
        '<a:ln w="%d" cap="flat"><a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
        '<a:tailEnd type="triangle" w="med" len="med"/></a:ln></p:spPr></p:cxnSp>'
        % (sid, _esc(item["name"]), flipH, flipV,
           _emu(ox + x), _emu(oy + y), _emu(max(cx, 0.01)), _emu(max(cy, 0.01)),
           int(round(item["width"] * 12700)), item["color"])
    )


def _pie(item, sid, ox, oy):
    r = item["r"]
    a0 = int(round(item["a0"] * 60000))
    a1 = int(round(item["a1"] * 60000))
    return (
        '<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="pie"><a:avLst><a:gd name="adj1" fmla="val %d"/>'
        '<a:gd name="adj2" fmla="val %d"/></a:avLst></a:prstGeom>'
        '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
        '<a:ln w="12700"><a:solidFill><a:srgbClr val="%s"/></a:solidFill></a:ln>'
        "</p:spPr></p:sp>"
        % (sid, _esc(item["name"]),
           _emu(ox + item["cx"] - r), _emu(oy + item["cy"] - r),
           _emu(2 * r), _emu(2 * r), a0, a1, item["color"], WHITE)
    )


def _pic(item, sid, rid, ox, oy):
    x, y, w, h = item["x"], item["y"], item["w"], item["h"]
    return (
        '<p:pic><p:nvPicPr><p:cNvPr id="%d" name="%s"/>'
        '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
        '<p:blipFill><a:blip r:embed="%s"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
        '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
        % (sid, _esc(item["name"]), rid,
           _emu(ox + x), _emu(oy + y), _emu(max(w, 0.05)), _emu(max(h, 0.05)))
    )


def to_xml(canvas, off_x_cm, off_y_cm, image_rids=None):
    """把画布序列化成形状 XML；id 从 900 起（模板里的形状 id 都很小）。

    image_rids 把画布里 kind=image 的项按出现顺序映射到关系 id。
    """
    parts = []
    sid = 900
    img_i = 0
    image_rids = image_rids or {}
    for item in canvas.items:
        if item["kind"] == "rect":
            parts.append(_sp(item, sid, off_x_cm, off_y_cm))
        elif item["kind"] == "arrow":
            parts.append(_arrow(item, sid, off_x_cm, off_y_cm))
        elif item["kind"] == "pie":
            parts.append(_pie(item, sid, off_x_cm, off_y_cm))
        elif item["kind"] == "image":
            rid = image_rids.get(img_i)
            img_i += 1
            if rid:
                parts.append(_pic(item, sid, rid, off_x_cm, off_y_cm))
        sid += 1
    return "".join(parts)


def canvas_images(canvas):
    """画布里按顺序出现的图片路径。"""
    if canvas is None:
        return []
    return [it["path"] for it in canvas.items if it.get("kind") == "image" and it.get("path")]


# ------------------------------------------------------------------
# 布局：从图对象算出形状
# ------------------------------------------------------------------
def _step(s):
    if isinstance(s, dict):
        children = s.get("children") or s.get("items") or []
        return {
            "label": str(s.get("label") or s.get("name") or ""),
            "desc": str(s.get("desc") or ""),
            "children": [str(c) for c in children if str(c).strip()],
        }
    return {"label": str(s), "desc": "", "children": []}


def _fit_label(text, box_w, box_h, hi):
    """色块标题、层名这类单行文字：按框能放下的最大字号，不超出框。"""
    return fit_pt([text], box_w, box_h, hi, PT_MIN)


def _title(cv, spec):
    title = str(spec.get("title") or "")
    if not title:
        return 0.10
    h = 0.62 if cv.h < 6 else 0.88
    pt = _fit_label(title, cv.w - PAD * 2, h - 0.14, spec.get("_pt") or PT_MAX)
    cv.text(PAD, 0.06, cv.w - PAD * 2, h - 0.14, [(title, pt, DK2, True)],
            align="l", anchor="ctr", name="图标题")
    return h


def _row_gap(n, cv_w):
    """节点之间的空隙。节点越多，空隙越大，保证箭头看得到。

    空隙至少 0.9 cm：箭头本身要占约 0.7 cm，再留出箭头两头不贴框的距离。
    节点少时放到 1.3 cm。空隙再大就会把节点挤得放不下一个词，到此为止。
    """
    if n <= 1:
        return GAP
    room = cv_w - PAD * 2
    gap = 1.30 if n <= 3 else 0.90
    # 每个节点至少留 1.6 cm，空隙再大就退回
    while gap > 0.70 and (room - gap * (n - 1)) / n < 1.6:
        gap -= 0.10
    return gap


def _row_items(items, cv_w, top, bottom, gap=None, pad=PAD):
    """把 n 个条目横排，返回 ([(x, w)], 单框宽, 高, 实际空隙)。"""
    n = max(1, len(items))
    if gap is None:
        gap = _row_gap(n, cv_w)
    usable = cv_w - pad * 2 - gap * (n - 1)
    w = usable / float(n)
    return [(pad + i * (w + gap), w) for i in range(n)], w, bottom - top, gap


def draw_flow(spec):
    """流程图。每步带 children 时画成分层流程，否则一排步骤框。"""
    cv = Canvas(spec.get('_w') or SLOT_W, spec.get('_h') or SLOT_H)
    # 半槽高度不够 16 磅，按槽高把图内字号上限降下来
    spec["_pt"] = PT_MAX
    top = _title(cv, spec)
    steps = [_step(s) for s in (spec.get("steps") or [])][:5]
    if not steps:
        return None
    layered = any(s["children"] for s in steps)
    boxes, bw, bh, row_gap = _row_items(steps, cv.w, top + 0.06, cv.h - PAD)
    bh = max(1.2, (cv.h - PAD) - (top + 0.06))
    for i, st in enumerate(steps):
        x = boxes[i][0]
        color = SERIES[i % len(SERIES)]
        cv.rect(x, top + 0.06, bw, bh, SOFT, color, 0.08, None, name="步骤框%d" % (i + 1))
        head = 0.62 if cv.h < 6 else 0.85
        cv.rect(x, top + 0.06, bw, head, color, color, 0.08, None, name="步骤头%d" % (i + 1))
        pt = _fit_label(st["label"], bw - 0.12, head, spec.get('_pt') or PT_MAX)
        cv.text(x, top + 0.06, bw, head, [(st["label"], pt, WHITE, True)],
                name="步骤名%d" % (i + 1))
        kids = st["children"] or ([st["desc"]] if st["desc"] else [])
        kids = kids[:3]
        if kids:
            # 子框要撑满步骤框剩下的高度：否则 11 cm 高的槽里挤着三个 1.05 cm 的小框，
            # 下面空出一大块白，看着像图没画完。
            inner_top = top + 0.06 + head + 0.18
            inner_bottom = cv.h - PAD - 0.10
            avail = max(0.9, inner_bottom - inner_top)
            n = len(kids)
            min_gap = 0.30 if avail > 4.0 else 0.14
            ch = max(0.95, min(2.80, (avail - min_gap * (n - 1)) / float(n)))
            gap = min_gap if n <= 1 else max(min_gap, (avail - ch * n) / float(n - 1))
            y = inner_top + max(0.0, (avail - (ch * n + gap * (n - 1))) / 2)
            for k, kid in enumerate(kids):
                pt = fit_pt([kid], bw - 0.24, ch - 0.08, spec.get('_pt') or PT_MAX, PT_MIN)
                # 子框不填色：万一和别的元素挨得近，也不会把文字盖住
                cv.rect(x + 0.12, y, bw - 0.24, ch, None, GRID, 0.04,
                        [(kid, pt, INK, False)], pt=pt, name="子步骤%d-%d" % (i + 1, k + 1))
                y += ch + gap
        if i < len(steps) - 1:
            ax = x + bw
            mid = top + 0.06 + bh / 2
            cv.arrow(ax + 0.06, mid, ax + row_gap - 0.06, mid, "4472C4", 1.8, "箭头%d" % (i + 1))
    return cv


def draw_framework(spec):
    """分层框架。层名单独占一条，内部条目框不填色，绝不遮住层名。"""
    cv = Canvas(spec.get('_w') or SLOT_W, spec.get('_h') or SLOT_H)
    # 半槽高度不够 16 磅，按槽高把图内字号上限降下来
    spec["_pt"] = PT_MAX
    top = _title(cv, spec)
    layers = (spec.get("layers") or [])[:4]
    if not layers:
        return None
    pad = PAD
    gap = 0.14
    total = cv.h - top - pad
    lh = (total - gap * (len(layers) - 1)) / float(len(layers))
    for i, layer in enumerate(layers):
        y = top + i * (lh + gap)
        color = SERIES[i % len(SERIES)]
        bg = SOFT if i % 2 == 0 else WHITE
        cv.rect(pad, y, cv.w - pad * 2, lh, bg, color, 0.08, None, name="层%d" % (i + 1))
        cv.rect(pad, y, 0.10, lh, color, None, 0, None, name="层条%d" % (i + 1))
        # 层名占一条独立的高度，条目框从它下面开始
        name_h = 0.46 if lh < 1.8 else 0.62
        name_pt = _fit_label(str(layer.get("name") or ""), cv.w - pad * 2 - 0.4, name_h,
                             spec.get("_pt") or PT_MAX)
        cv.text(pad + 0.24, y + 0.04, cv.w - pad * 2 - 0.4, name_h,
                [(str(layer.get("name") or ""), name_pt, color, True)],
                align="l", anchor="ctr", name="层名%d" % (i + 1))
        items = [str(x) for x in (layer.get("items") or [])][:4]
        if not items:
            continue
        inset = 0.16
        inner_top = y + name_h + 0.10
        inner_h = lh - name_h - 0.24
        avail_w = cv.w - pad * 2 - inset * 2
        cw = (avail_w - 0.12 * (len(items) - 1)) / float(len(items))
        for j, it in enumerate(items):
            x = pad + inset + j * (cw + 0.12)
            pt, need_h = fit_box([it], cw, inner_h, spec.get('_pt') or PT_MAX, PT_MIN)
            use_h = min(lh - (inner_top - y) - 0.06, max(inner_h, need_h))
            cv.rect(x, inner_top, cw, use_h, None, GRID, 0.05,
                    [(it, pt, INK, False)], pt=pt, name="条目%d-%d" % (i + 1, j + 1))
    return cv


def draw_logic(spec):
    """逻辑链：若干环节用箭头串起来，底部一条结论带。"""
    cv = Canvas(spec.get('_w') or SLOT_W, spec.get('_h') or SLOT_H)
    # 半槽高度不够 16 磅，按槽高把图内字号上限降下来
    spec["_pt"] = PT_MAX
    top = _title(cv, spec)
    nodes = [_step(s) for s in (spec.get("nodes") or spec.get("steps") or [])][:4]
    if not nodes:
        return None
    foot = spec.get("foot")
    foot_h = 0.95 if foot else 0.0
    foot_gap = 0.20 if foot else 0.0
    bottom = cv.h - PAD - foot_h - foot_gap
    boxes, bw, bh, row_gap = _row_items(nodes, cv.w, top + 0.04, bottom)
    bh = max(1.2, bottom - (top + 0.04))
    for i, nd in enumerate(nodes):
        x = boxes[i][0]
        color = SERIES[i % len(SERIES)]
        cv.rect(x, top + 0.04, bw, bh, WHITE, color, 0.08, None, name="环节%d" % (i + 1))
        head = 0.58 if cv.h < 6 else 0.80
        cv.rect(x, top + 0.04, bw, head, color, color, 0.08, None, name="环节头%d" % (i + 1))
        pt = _fit_label(nd["label"], bw - 0.12, head, spec.get('_pt') or PT_MAX)
        cv.text(x, top + 0.04, bw, head, [(nd["label"], pt, WHITE, True)],
                name="环节名%d" % (i + 1))
        desc = nd["desc"]
        if desc:
            body_top = top + 0.04 + head + 0.12
            body_h = bh - head - 0.24
            pt, need_h = fit_box([desc], bw - 0.24, body_h, spec.get('_pt') or PT_MAX, PT_MIN)
            body_h = min(bh - head - 0.24, max(body_h, need_h))
            cv.text(x + 0.10, body_top, bw - 0.20, body_h,
                    [(desc, pt, INK, False)], anchor="ctr", name="环节说明%d" % (i + 1))
        if i < len(nodes) - 1:
            ax = x + bw
            mid = top + 0.04 + bh / 2
            cv.arrow(ax + 0.06, mid, ax + row_gap - 0.06, mid, "4472C4", 1.8, "箭头%d" % (i + 1))
    if foot:
        fy = cv.h - PAD - foot_h
        cv.rect(PAD, fy, cv.w - PAD * 2, foot_h, SOFT, "5B9BD5", 0.08,
                [(str(foot), _fit_label(str(foot), cv.w - PAD * 2, foot_h, spec.get('_pt') or PT_MAX), DK2, True)],
                name="结论带")
    return cv


def draw_cards(spec):
    cv = Canvas(spec.get('_w') or SLOT_W, spec.get('_h') or SLOT_H)
    # 半槽高度不够 16 磅，按槽高把图内字号上限降下来
    spec["_pt"] = PT_MAX
    top = _title(cv, spec)
    items = (spec.get("items") or [])[:4]
    if not items:
        return None
    n = len(items)
    cols = 2 if n > 1 else 1
    rows = int(math.ceil(n / float(cols)))
    pad = PAD
    gap = 0.18
    gw = cv.w - pad * 2
    gh = cv.h - top - pad
    cw = (gw - gap * (cols - 1)) / float(cols)
    ch = (gh - gap * (rows - 1)) / float(rows)
    for i, it in enumerate(items):
        r, c = divmod(i, cols)
        x = pad + c * (cw + gap)
        y = top + r * (ch + gap)
        color = SERIES[i % len(SERIES)]
        title = str(it.get("title") or "")
        cv.rect(x, y, cw, ch, SOFT, color, 0.08, None, name="卡片%d" % (i + 1))
        cv.rect(x, y, 0.10, ch, color, None, 0, None, name="卡片条%d" % (i + 1))
        head_h = 0.62
        cv.text(x + 0.26, y + 0.06, cw - 0.4, head_h,
                [(title, _fit_label(title, cw - 0.4, head_h, spec.get('_pt') or PT_MAX), color, True)],
                align="l", anchor="ctr", name="卡片名%d" % (i + 1))
        lines = [str(s) for s in (it.get("lines") or [])][:3]
        if not lines:
            continue
        body_top = y + head_h + 0.10
        body_h = ch - head_h - 0.24
        pt, need_h = fit_box(lines, cw - 0.5, body_h, spec.get('_pt') or PT_MAX, PT_MIN)
        body_h = min(ch - head_h - 0.20, max(body_h, need_h))
        cv.text(x + 0.26, body_top, cw - 0.46, body_h,
                [(ln, pt, INK, False) for ln in lines], align="l", anchor="t",
                name="卡片内容%d" % (i + 1))
    return cv


def draw_compare(spec):
    cv = Canvas(spec.get('_w') or SLOT_W, spec.get('_h') or SLOT_H)
    # 半槽高度不够 16 磅，按槽高把图内字号上限降下来
    spec["_pt"] = PT_MAX
    top = _title(cv, spec)
    left = spec.get("left") or {}
    right = spec.get("right") or {}
    pad = PAD
    gap = 0.24
    cw = (cv.w - pad * 2 - gap) / 2.0
    ch = cv.h - top - pad
    for idx, (side, color, fill) in enumerate(
        ((left, "A5A5A5", SOFT_ALT), (right, "5B9BD5", SOFT))
    ):
        x = pad + idx * (cw + gap)
        y = top
        cv.rect(x, y, cw, ch, fill, color, 0.08, None, name="对比栏%d" % idx)
        head = 0.58 if cv.h < 6 else 0.80
        cv.rect(x, y, cw, head, color, color, 0.08, None, name="对比头%d" % idx)
        cv.text(x, y, cw, head, [(str(side.get("title") or ""),
                _fit_label(str(side.get("title") or ""), cw - 0.12, head, spec.get('_pt') or PT_MAX), WHITE, True)],
                name="对比名%d" % idx)
        points = [str(p) for p in (side.get("points") or [])][:4]
        if not points:
            continue
        body_top = y + head + 0.18
        body_h = ch - head - 0.36
        shown = ["· " + p for p in points]
        pt, need_h = fit_box(shown, cw - 0.7, body_h, spec.get('_pt') or PT_MAX, PT_MIN)
        body_h = min(ch - head - 0.30, max(body_h, need_h))
        cv.text(x + 0.34, body_top, cw - 0.6, body_h,
                [(p, pt, INK, False) for p in shown], align="l", anchor="t",
                name="对比内容%d" % idx)
    return cv


def draw_table(spec):
    cv = Canvas(spec.get('_w') or SLOT_W, spec.get('_h') or SLOT_H)
    # 半槽高度不够 16 磅，按槽高把图内字号上限降下来
    spec["_pt"] = PT_MAX
    top = _title(cv, spec)
    columns = [str(c) for c in (spec.get("columns") or [])]
    rows = spec.get("rows") or []
    if not columns and rows:
        columns = [str(c) for c in rows[0]]
        rows = rows[1:]
    if not columns:
        return None
    n_col = len(columns)
    rows = [[str(c) for c in (list(r) + [""] * n_col)[:n_col]] for r in rows][:5]
    pad = PAD
    x0, y0 = pad, top
    tw = cv.w - pad * 2
    th = cv.h - top - pad
    cw = tw / float(n_col)
    head_h = min(0.95, th * 0.22)
    row_h = (th - head_h) / float(max(len(rows), 1))
    for i, col in enumerate(columns):
        cv.rect(x0 + i * cw, y0, cw, head_h, "5B9BD5", "5B9BD5", 0,
                [(col, _fit_label(col, cw - 0.1, head_h, spec.get('_pt') or PT_MAX), WHITE, True)],
                name="表头%d" % (i + 1))
    for r, row in enumerate(rows):
        y = y0 + head_h + r * row_h
        bg = WHITE if r % 2 == 0 else SOFT
        for i, cell in enumerate(row):
            pt = fit_pt([cell], cw - 0.3, row_h - 0.1, spec.get('_pt') or PT_MAX, PT_MIN)
            cv.rect(x0 + i * cw, y, cw, row_h, bg, GRID, 0,
                    [(cell, pt, INK, False)], pt=pt, name="单元格%d-%d" % (r + 1, i + 1))
    return cv


def draw_bar(spec):
    cv = Canvas(spec.get('_w') or SLOT_W, spec.get('_h') or SLOT_H)
    # 半槽高度不够 16 磅，按槽高把图内字号上限降下来
    spec["_pt"] = PT_MAX
    top = _title(cv, spec)
    unit = spec.get("unit") or ""
    items = (spec.get("items") or [])[:6]
    if not items:
        return None
    label_w = 4.2
    pad = PAD
    chart_x = pad + label_w
    chart_w = cv.w - chart_x - pad - 1.6
    chart_y = top + 0.12
    chart_h = cv.h - chart_y - pad
    n = len(items)
    row_h = chart_h / float(n)
    grouped = "values" in items[0]
    max_v = max(
        max(float(v) for v in it.get("values") or [0]) if grouped else float(it.get("value") or 0)
        for it in items
    ) or 1.0
    for i, it in enumerate(items):
        y = chart_y + i * row_h
        label = str(it.get("label") or "")
        pt = fit_pt([label], label_w - 0.2, row_h - 0.08, spec.get('_pt') or PT_MAX, PT_MIN)
        cv.text(pad, y, label_w - 0.12, row_h, [(label, pt, INK, False)],
                align="r", anchor="ctr", name="项目%d" % (i + 1))
        if grouped:
            vals = [float(v) for v in (it.get("values") or [])]
            m = max(len(vals), 1)
            bh = min(0.52, (row_h - 0.16) / m)
            by = y + (row_h - bh * m - 0.06 * (m - 1)) / 2
            for s, val in enumerate(vals):
                color = SERIES[s % len(SERIES)]
                bw = max(0.05, chart_w * val / max_v)
                cv.rect(chart_x, by, bw, bh, color, None, 0.03, None, name="条%d-%d" % (i + 1, s + 1))
                cv.text(chart_x + bw + 0.06, by, 1.5, bh,
                        [(_fmt(val, unit), 14, INK, False)], align="l", anchor="ctr",
                        name="值%d-%d" % (i + 1, s + 1))
                by += bh + 0.06
        else:
            val = float(it.get("value") or 0)
            bh = min(0.62, row_h * 0.5)
            by = y + (row_h - bh) / 2
            bw = max(0.05, chart_w * val / max_v)
            cv.rect(chart_x, by, bw, bh, SERIES[i % 3] if n <= 3 else "5B9BD5", None, 0.04,
                    None, name="条%d" % (i + 1))
            cv.text(chart_x + bw + 0.08, by, 1.6, bh,
                    [(_fmt(val, unit), 14, INK, False)], align="l", anchor="ctr",
                    name="值%d" % (i + 1))
    return cv


def draw_pie(spec):
    cv = Canvas(spec.get('_w') or SLOT_W, spec.get('_h') or SLOT_H)
    # 半槽高度不够 16 磅，按槽高把图内字号上限降下来
    spec["_pt"] = PT_MAX
    top = _title(cv, spec)
    items = (spec.get("items") or [])[:5]
    if not items:
        return None
    total = sum(float(it.get("value") or 0) for it in items) or 1.0
    # 饼按槽位长出来，不再一刀切 3.2 cm：整槽里 3.2 cm 的饼只占一角，
    # 下半边空着。左半幅留给饼，右半幅留给图例。
    r = max(1.4, min((cv.h - top - PAD * 2) / 2.0, (cv.w * 0.46 - PAD - 0.4) / 2.0))
    cx = PAD + r + 0.4
    cy = top + (cv.h - top - PAD) / 2.0
    a = -90.0
    for i, it in enumerate(items):
        val = float(it.get("value") or 0)
        extent = 360.0 * val / total
        cv.sector(cx, cy, r, a, a + extent, SERIES[i % len(SERIES)], "扇区%d" % (i + 1))
        a += extent
    lx = cx + r + 0.7
    lw = cv.w - lx - PAD
    n = len(items)
    row_h = min(1.15, (cv.h - top - PAD) / float(n))
    # 图例整体与饼居中对齐，不从顶上往下排：否则饼在中间、图例贴着顶，
    # 右下角空出一大块。
    y = max(top + 0.14, cy - row_h * n / 2.0)
    for i, it in enumerate(items):
        color = SERIES[i % len(SERIES)]
        val = float(it.get("value") or 0)
        pct = "%.0f%%" % (100.0 * val / total)
        cv.rect(lx, y + 0.16, 0.26, 0.26, color, None, 0.02, None, name="图例色%d" % (i + 1))
        cv.text(lx + 0.40, y, lw - 0.4, row_h * 0.55,
                [("%s  %s" % (it.get("label") or "", pct),
                  _fit_label("%s  %s" % (it.get("label") or "", pct), lw - 0.4, row_h * 0.55,
                             spec.get('_pt') or PT_MAX), INK, False)],
                align="l", anchor="ctr", name="图例%d" % (i + 1))
        cv.text(lx + 0.40, y + row_h * 0.52, lw - 0.4, row_h * 0.4,
                [(_fmt(val, spec.get("unit") or ""), 14, MUTED, False)],
                align="l", anchor="ctr", name="图例值%d" % (i + 1))
        y += row_h
    return cv


def draw_gantt(spec):
    """甘特图。时间轴单独占一条，图表从它下面开始，柱子不会压到轴标签。"""
    cv = Canvas(spec.get('_w') or SLOT_W, spec.get('_h') or SLOT_H)
    # 半槽高度不够 16 磅，按槽高把图内字号上限降下来
    spec["_pt"] = PT_MAX
    top = _title(cv, spec)
    axis = [str(a) for a in (spec.get("axis") or [])]
    items = (spec.get("items") or [])[:6]
    if not axis or not items:
        return None
    pad = PAD
    label_w = 3.6
    axis_pt = 15
    axis_h = axis_pt * 1.35 / 28.35 + 0.12  # 轴标签自己的高度
    chart_x = pad + label_w
    chart_w = cv.w - chart_x - pad
    axis_y = top + 0.06
    chart_y = axis_y + axis_h + 0.10
    chart_h = cv.h - chart_y - pad
    if chart_h < 0.6:
        chart_h = 0.6
        chart_y = cv.h - pad - chart_h
    n = len(axis)
    cw = chart_w / float(n)
    for i, a in enumerate(axis):
        x = chart_x + i * cw
        cv.text(x, axis_y, cw, axis_h, [(a, axis_pt, MUTED, False)],
                name="轴%d" % (i + 1))
    row_h = chart_h / float(len(items))
    for r, it in enumerate(items):
        y = chart_y + r * row_h
        if r % 2 == 0:
            cv.rect(chart_x, y, chart_w, row_h, SOFT, None, 0, None, name="行底%d" % (r + 1))
        name = str(it.get("name") or it.get("label") or "")
        pt = fit_pt([name], label_w - 0.2, row_h - 0.08, spec.get('_pt') or PT_MAX, PT_MIN)
        cv.text(pad, y, label_w - 0.14, row_h, [(name, pt, INK, False)],
                align="l", anchor="ctr", name="阶段%d" % (r + 1))
        # span 给的是起止列号（含两端），start/end 是按列计的小数
        span = it.get("span")
        if isinstance(span, (list, tuple)) and len(span) == 2:
            start, end = float(span[0]), float(span[1]) + 1
        else:
            start = float(it.get("start") or 0)
            end = float(it.get("end") if it.get("end") is not None else start + 1)
        x0 = chart_x + start * cw + 0.06
        x1 = chart_x + end * cw - 0.06
        bh = min(0.60, row_h * 0.5)
        by = y + (row_h - bh) / 2
        cv.rect(x0, by, max(0.12, x1 - x0), bh, SERIES[r % 3], None, 0.06, None,
                name="任务条%d" % (r + 1))
    return cv


def _fmt(val, unit):
    if abs(val - round(val)) < 1e-6:
        s = str(int(round(val)))
    else:
        s = ("%.2f" % val).rstrip("0").rstrip(".")
    return s + (unit or "")


def normalize_figure(fig):
    """把 content.json 里的 figure 字段收成 dict；无法绘制时返回 None。

    字符串给的是路径就当原图；"图/表" 是模板占位，不算内容。
    """
    import os

    if fig is None or fig == "" or fig == "图/表":
        return None
    if isinstance(fig, str):
        if os.path.exists(fig):
            return {"type": "image", "path": fig}
        return None
    if not isinstance(fig, dict):
        return None
    t = fig.get("type")
    if t not in RENDERERS and t != "image":
        if fig.get("path") or fig.get("image"):
            fig = dict(fig)
            fig["type"] = "image"
            return fig
        return None
    return fig


def render_image(spec, out_path, dpi=260):
    """把申请书原图按图表槽尺寸缩放居中，写成 PNG。

    原图本来就是一张现成的图，不重画、也不改它的内部结构；
    只做「按槽位缩放 + 居中 + 加图题」。槽位里放不下时等比缩放，
    不拉伸。需要 Pillow。
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    import os

    path = spec.get("path") or spec.get("image") or ""
    caption = spec.get("caption") or spec.get("title") or ""
    if not path or not os.path.exists(path):
        return None

    from PIL import ImageDraw

    W = int(round(SLOT_W / 2.54 * dpi))
    H = int(round(SLOT_H / 2.54 * dpi))
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    top = 10
    if caption:
        f = _pil_font(20, dpi, bold=True)
        bb = d.textbbox((0, 0), caption, font=f)
        d.text((14, 8), caption, font=f, fill="#" + DK2)
        top = 8 + (bb[3] - bb[1]) + 12

    src = Image.open(path).convert("RGBA")
    box_w, box_h = W - 24, H - top - 16
    src.thumbnail((max(16, box_w), max(16, box_h)), Image.Resampling.LANCZOS)
    px = 12 + (box_w - src.size[0]) // 2
    py = top + (box_h - src.size[1]) // 2
    img.paste(src, (px, py), src if src.mode == "RGBA" else None)
    directory = os.path.dirname(out_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def _pil_font(pt_size, dpi, bold=False):
    from PIL import ImageFont
    import os

    for cand in FONT_CANDIDATES:
        if os.path.exists(cand):
            try:
                return ImageFont.truetype(cand, max(8, int(round(pt_size * dpi / 72.0))))
            except OSError:
                break
    return ImageFont.load_default()


def _image_size(path):
    """图片像素尺寸 (w, h)；读不到返回 (None, None)。

    SVG 没有像素尺寸，从 width/height 或 viewBox 里取比例，够用来等比摆放。
    """
    import os
    import re as _re

    if not path or not os.path.exists(path):
        return (None, None)
    if os.path.splitext(path)[1].lower() == ".svg":
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                head = fh.read(4096)
        except OSError:
            return (None, None)
        m = _re.search(r'viewBox\s*=\s*"[\s\d.+-]*?([\d.]+)[\s,]+([\d.]+)\s*"', head)
        if m:
            try:
                return (float(m.group(1)), float(m.group(2)))
            except ValueError:
                pass
        wm = _re.search(r'\bwidth\s*=\s*"([\d.]+)', head)
        hm = _re.search(r'\bheight\s*=\s*"([\d.]+)', head)
        if wm and hm:
            try:
                return (float(wm.group(1)), float(hm.group(1)))
            except ValueError:
                pass
        return (None, None)
    try:
        from PIL import Image
    except ImportError:
        return (None, None)
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (None, None)


def image_effective_dpi(path, box_w_cm, box_h_cm):
    """这张图铺进 box_w × box_h（cm）之后的有效 DPI。

    用来判断「素材看着小」到底是被原文件放小了，还是像素真的不够：
    像素够就放大铺满，不够才改为重画。SVG 是矢量，返回 None 表示不受此限。

    按「铺满框」（两个方向都不留白）取保守值：一个方向上每英寸的像素数
    取两者较小的那个，与 audit_process.py 的判据一致。
    例：1560×1120 的图铺进 17×11 cm → min(233, 259) = 233 DPI。
    """
    import os

    if path and os.path.splitext(path)[1].lower() == ".svg":
        return None
    iw, ih = _image_size(path)
    if not iw or not ih or box_w_cm <= 0 or box_h_cm <= 0:
        return None
    return min(iw * 2.54 / box_w_cm, ih * 2.54 / box_h_cm)


def external_image(path, box_w_cm, box_h_cm, name="外部图"):
    """把外部画好的位图/SVG 等比放进给定框，返回一个 image 画布项。

    用于复杂图先用绘图工具出 PNG/SVG、再嵌进 PPT 的情形（见 figure-spec 第一节第 3 条）。
    等比摆放，不拉伸；读不到尺寸就铺满框。
    """
    iw, ih = _image_size(path)
    if iw and ih:
        scale = min(box_w_cm / float(iw), box_h_cm / float(ih))
        w, h = iw * scale, ih * scale
    else:
        w, h = box_w_cm, box_h_cm
    return {
        "kind": "image",
        "path": path,
        "x": (box_w_cm - w) / 2.0,
        "y": (box_h_cm - h) / 2.0,
        "w": w,
        "h": h,
        "name": name,
    }


def draw_logos(spec):
    """一组 logo 或证明材料缩略图，排成一行或两行。

    items 每项可以是图片路径字符串，或 {"path": ..., "label": ...}。
    路径打不开的项跳过，不留空框；一张都打不开时退回文字名录。
    """
    import os

    items = spec.get("items") or []
    entries = []
    for it in items:
        if isinstance(it, str):
            entries.append({"path": it, "label": ""})
        elif isinstance(it, dict):
            entries.append({
                "path": it.get("path") or it.get("image") or "",
                "label": str(it.get("label") or it.get("name") or ""),
            })
    cv = Canvas(spec.get('_w') or SLOT_W, spec.get('_h') or SLOT_H)
    # 半槽高度不够 16 磅，按槽高把图内字号上限降下来
    spec["_pt"] = PT_MAX
    top = _title(cv, spec)
    usable = [e for e in entries if e["path"] and os.path.exists(e["path"])]
    missing = [e for e in entries if e not in usable]
    show = usable or [{"path": "", "label": e["label"] or "缺图"} for e in missing]
    if not show:
        return cv
    cols = min(len(show), 4 if len(show) > 4 else len(show))
    rows = (len(show) + cols - 1) // cols
    gap = 0.28
    cell_w = (cv.w - PAD * 2 - gap * (cols - 1)) / cols
    cell_h = (cv.h - top - PAD - gap * (rows - 1)) / rows
    for i, entry in enumerate(show):
        r, c = divmod(i, cols)
        x = PAD + c * (cell_w + gap)
        y = top + r * (cell_h + gap)
        label = entry["label"]
        label_h = 0.7 if label else 0
        cv.rect(x, y, cell_w, cell_h, None, "5B9BD5", 0.08, name="logo框")
        if entry["path"]:
            # 标识必须等比：按原图像素比算出在格子里的实际宽高再居中，
            # 不把图拉满格子——商标拉变形比图小一点严重得多。
            box_w = cell_w - 0.24
            box_h = cell_h - label_h - 0.24
            iw, ih = _image_size(entry["path"])
            if iw and ih:
                scale = min(box_w / float(iw), box_h / float(ih))
                draw_w, draw_h = iw * scale, ih * scale
            else:
                draw_w, draw_h = box_w, box_h
            cv.items.append({
                "kind": "image",
                "path": entry["path"],
                "x": x + 0.12 + (box_w - draw_w) / 2.0,
                "y": y + 0.12 + (box_h - draw_h) / 2.0,
                "w": draw_w,
                "h": draw_h,
                "name": "logo",
            })
        if label:
            cv.text(
                x, y + cell_h - label_h, cell_w, label_h,
                [(label, 12, INK, False)], anchor="ctr", name="logo名",
            )
    return cv


RENDERERS = {
    "logos": draw_logos,
    "flow": draw_flow,
    "framework": draw_framework,
    "logic": draw_logic,
    "cards": draw_cards,
    "compare": draw_compare,
    "table": draw_table,
    "bar": draw_bar,
    "pie": draw_pie,
    "gantt": draw_gantt,
}


def text_overflows(canvas):
    """文本框装不下文字的项：(形状名, 文字, 需要高度, 实际高度)。"""
    if canvas is None:
        return []
    return list(getattr(canvas, "overflows", []))


# 边界容差（cm）：描边有宽度，共边的两个框会算出极小的交集。
OVERLAP_TOL = 0.03
# 判定「真的压上了」的最小重合量（cm）。竖排的文字行、紧贴的表格单元格之间
# 常有几丝交集，那是共边不是遮挡；两个方向都超过这个值才算事故。
OVERLAP_MIN_CM = 0.12


def _box(item):
    if item.get("kind") in ("rect", "image"):
        return (item["x"], item["y"], item["x"] + item["w"], item["y"] + item["h"])
    return None


def _contains(outer, inner, tol=OVERLAP_TOL):
    return (
        outer[0] - tol <= inner[0] and outer[1] - tol <= inner[1]
        and outer[2] + tol >= inner[2] and outer[3] + tol >= inner[3]
    )


def _intersection(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0, 0.0
    return w, h


def shape_overlaps(canvas):
    """图内互相遮挡的形状对：[(名A, 名B, 重合宽, 重合高)]。

    分层框架、卡片这类图本来就是「外层框套内层框」，包含关系不算遮挡；
    只报两个互不包含的框真的压在一起的情况。带文字的框被压上才是事故，
    所以两个都没文字的纯底色块之间不报。
    """
    if canvas is None:
        return []
    items = [it for it in canvas.items if _box(it)]
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            ba, bb = _box(a), _box(b)
            w, h = _intersection(ba, bb)
            # 两个方向都真的压进去了才算遮挡：只在一个方向上有几丝交集的，
            # 是竖排文字行或相邻单元格共边。
            if w <= OVERLAP_MIN_CM or h <= OVERLAP_MIN_CM:
                continue
            if _contains(ba, bb) or _contains(bb, ba):
                continue
            a_text = bool(a.get("texts")) or a.get("kind") == "image"
            b_text = bool(b.get("texts")) or b.get("kind") == "image"
            if not (a_text or b_text):
                continue
            out.append((a.get("name") or "形状", b.get("name") or "形状",
                        round(w, 2), round(h, 2)))
    return out


def out_of_canvas(canvas):
    """超出画布边界的形状：[(名, 左, 上, 右, 下)]。"""
    if canvas is None:
        return []
    out = []
    for it in canvas.items:
        box = _box(it)
        if not box:
            continue
        if (box[0] < -OVERLAP_TOL or box[1] < -OVERLAP_TOL
                or box[2] > canvas.w + OVERLAP_TOL or box[3] > canvas.h + OVERLAP_TOL):
            out.append((it.get("name") or "形状", round(box[0], 2), round(box[1], 2),
                        round(box[2], 2), round(box[3], 2)))
    return out


def blank_ratio(canvas):
    """画布里没有被任何形状覆盖的面积占比（粗粒度网格采样）。

    用来发现「图只占了槽位上半截、下面空一大片」这种画了但没画满的情形。
    """
    if canvas is None or canvas.w <= 0 or canvas.h <= 0:
        return 0.0
    boxes = [_box(it) for it in canvas.items]
    boxes = [b for b in boxes if b]
    # 饼图的扇区不是矩形，按外接圆算覆盖，否则一张画好的饼图会被判成「几乎全空」
    circles = [(it["cx"], it["cy"], it["r"]) for it in canvas.items
               if it.get("kind") == "pie" and it.get("r")]
    if not boxes and not circles:
        return 1.0
    cols, rows = 40, 24
    cw, ch = canvas.w / cols, canvas.h / rows
    covered = 0
    for r in range(rows):
        cy = (r + 0.5) * ch
        for c in range(cols):
            cx = (c + 0.5) * cw
            hit = False
            for b in boxes:
                if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:
                    hit = True
                    break
            if not hit:
                for ox, oy, rad in circles:
                    if (cx - ox) ** 2 + (cy - oy) ** 2 <= rad * rad:
                        hit = True
                        break
            if hit:
                covered += 1
    return 1.0 - covered / float(cols * rows)


def render_shapes(spec):
    """返回画布；type 不受支持或数据缺失时返回 None（由调用方退回位图）。"""
    if not isinstance(spec, dict):
        return None
    fn = RENDERERS.get(spec.get("type"))
    if fn is None:
        return None
    try:
        return fn(spec)
    except Exception:
        return None


# ------------------------------------------------------------------
# 自检用预览：把画布按同一套几何画成 PNG，用来肉眼核对版式。
# PowerPoint 里的实际效果以形状为准，这里只用来查「有没有压字、出框」。
# ------------------------------------------------------------------
def to_preview_png(canvas, out_path, dpi=150):
    """把画布渲染成预览 PNG。需要 Pillow；没装就返回 None。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    scale = dpi / 2.54
    W = int(canvas.w * scale)
    H = int(canvas.h * scale)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    import os

    font_path = next((c for c in FONT_CANDIDATES if os.path.exists(c)), None)

    cache = {}

    def fnt(pt):
        key = int(pt)
        if key not in cache:
            px = max(8, int(round(pt / 72.0 * dpi)))
            try:
                cache[key] = ImageFont.truetype(font_path, px)
            except Exception:
                cache[key] = ImageFont.load_default()
        return cache[key]

    def P(v):
        return v * scale

    for item in canvas.items:
        if item["kind"] == "rect":
            x, y = P(item["x"]), P(item["y"])
            x2, y2 = P(item["x"] + item["w"]), P(item["y"] + item["h"])
            if item["fill"]:
                d.rectangle([x, y, x2, y2], fill="#" + item["fill"])
            if item["line"]:
                d.rectangle([x, y, x2, y2], outline="#" + item["line"],
                            width=max(1, int(P(item["line_w"] / 28.35))))
            if item["texts"]:
                f = fnt(item["pt"] if item["texts"][0][1] is None else item["texts"][0][1])
                ty = y + P(0.06)
                for t in item["texts"]:
                    f = fnt(t[1] if len(t) > 1 else item["pt"])
                    color = "#" + (t[2] if len(t) > 2 else INK)
                    lines = _wrap_preview(t[0], f, item["w"] - 0.12, scale)
                    for ln in lines:
                        tw = d.textlength(ln, font=f)
                        if item["align"] == "l":
                            tx = x + P(0.06)
                        elif item["align"] == "r":
                            tx = x2 - tw - P(0.06)
                        else:
                            tx = x + (P(item["w"]) - tw) / 2.0
                        d.text((tx, ty), ln, font=f, fill=color)
                        ty += f.size * 1.3
        elif item["kind"] == "arrow":
            x1, y1 = P(item["x1"]), P(item["y1"])
            x2, y2 = P(item["x2"]), P(item["y2"])
            w = max(1, int(P(item["width"] / 28.35)))
            d.line([x1, y1, x2, y2], fill="#" + item["color"], width=w)
            import math as _m

            ang = _m.atan2(y2 - y1, x2 - x1)
            L = P(0.16)
            d.polygon(
                [
                    (x2, y2),
                    (x2 - L * _m.cos(ang - 0.45), y2 - L * _m.sin(ang - 0.45)),
                    (x2 - L * _m.cos(ang + 0.45), y2 - L * _m.sin(ang + 0.45)),
                ],
                fill="#" + item["color"],
            )
        elif item["kind"] == "pie":
            r = P(item["r"])
            cx, cy = P(item["cx"]), P(item["cy"])
            box = [cx - r, cy - r, cx + r, cy + r]
            d.pieslice(box, item["a0"], item["a1"], fill="#" + item["color"],
                       outline="white", width=2)

    img.save(out_path, "PNG")
    return out_path


# 行首禁则：与 PowerPoint 的中文避头尾一致，预览才不会被误判成"标点跑到行首"
_NO_LINE_START = "，。、；：！？）】》」』”’%…·,.;:!?)]}"


def _wrap_preview(text, fnt, box_w_cm, scale):
    max_w = box_w_cm * scale
    out = []
    for para in str(text).split("\n"):
        cur = ""
        for ch in para:
            if _text_w(fnt, cur + ch) <= max_w or not cur:
                cur += ch
            elif ch in _NO_LINE_START:
                cur += ch
            else:
                out.append(cur)
                cur = ch
        out.append(cur)
    return out or [""]


_MEASURE = None


def _text_w(fnt, text):
    global _MEASURE
    if _MEASURE is None:
        from PIL import Image, ImageDraw

        _MEASURE = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    return _MEASURE.textlength(text, font=fnt)
