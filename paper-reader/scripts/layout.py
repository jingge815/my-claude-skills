"""幻灯片布局引擎：把 content.json 里的一页 slide 变成一组带绝对坐标的绘制元素。

PPT 生成（build_pptx.py）和视频帧渲染（render_frames.py）都消费这里的输出，
所以两边的位置、字号、折行完全一致。

元素类型（坐标单位英寸，字号单位磅）:
  rect  : x y w h fill line radius
  text  : x y w h paras valign  -> 布局后补充 fitted=[{lines, size, ...}]、overflow
  image : x y w h file          -> 已按原图比例缩放并居中
  table : x y w col_w rows header size -> 布局后补充 row_h、fitted 单元格
"""
from __future__ import annotations

from PIL import Image

from common import (SLIDE_H, SLIDE_W, THEME, line_height, parse_rich, plain,
                    resolve_figure, text_width, wrap_runs)

MARGIN_X = 0.6
CONTENT_TOP = 1.45
CONTENT_BOTTOM = 6.8
FOOTER_Y = 7.02

BULLET_INDENT = {0: 0.34, 1: 0.78}  # 文字左边距（悬挂缩进）
BULLET_CHAR = {0: "■", 1: "–"}
BULLET_CHAR_SCALE = {0: 0.55, 1: 1.0}


class SlideError(ValueError):
    pass


# ================================================================ 文本排版
def para(text, size, color=None, bold=False, align="l", bullet=None, level=0,
         space_before=0.0, italic=False):
    return {"text": text, "size": size, "color": color or THEME["text"], "bold": bold,
            "align": align, "bullet": bullet, "level": level,
            "space_before": space_before, "italic": italic}


def _layout_paras(paras, width, scale):
    """按缩放比例排版，返回 (fitted, 总高度)。"""
    fitted, total = [], 0.0
    for i, p in enumerate(paras):
        size = max(8.0, round(p["size"] * scale * 2) / 2)
        indent = BULLET_INDENT[p["level"]] if p["bullet"] else 0.0
        runs = parse_rich(p["text"])
        if p["bold"]:
            runs = [(t, True) for t, _ in runs]
        lines = wrap_runs(runs, size, max(0.3, width - indent))
        lh = line_height(size)
        sb = p["space_before"] * scale / 72.0 if i > 0 else 0.0
        h = sb + lh * len(lines)
        fitted.append({**p, "size": size, "lines": lines, "indent": indent,
                       "line_h": lh, "space_before_in": sb, "height": h})
        total += h
    return fitted, total


def text_el(x, y, w, h, paras, valign="t", min_scale=0.62, fit=True):
    """创建文本框并自适应字号：放不下就整体按比例缩小，直到 min_scale。"""
    el = {"kind": "text", "x": x, "y": y, "w": w, "h": h, "paras": paras, "valign": valign}
    scale = 1.0
    while True:
        fitted, total = _layout_paras(paras, w, scale)
        if total <= h + 1e-6 or not fit or scale <= min_scale:
            break
        scale = max(min_scale, scale - 0.04)
    el.update(fitted=fitted, content_h=total, scale=scale, overflow=total > h + 0.02)
    return el


def measure_paras(paras, width, scale=1.0):
    return _layout_paras(paras, width, scale)[1]


def rect(x, y, w, h, fill=None, line=None, radius=0.0, line_w=1.0):
    return {"kind": "rect", "x": x, "y": y, "w": w, "h": h, "fill": fill, "line": line,
            "radius": radius, "line_w": line_w}


def image_el(file, x, y, w, h, align="c"):
    """按原图比例放进 (x,y,w,h)，返回实际占位。"""
    with Image.open(file) as im:
        iw, ih = im.size
    s = min(w / iw, h / ih)
    dw, dh = iw * s, ih * s
    dx = x + (w - dw) / 2 if align == "c" else x
    dy = y + (h - dh) / 2
    return {"kind": "image", "file": file, "x": dx, "y": dy, "w": dw, "h": dh}


def image_aspect(file) -> float:
    with Image.open(file) as im:
        return im.width / im.height


# ================================================================ 公共部件
def bullet_paras(items, size=20, sub_size=None, gap=10):
    """bullets 支持：字符串，或 {"text": "...", "sub": ["...", ...]}。"""
    sub_size = sub_size or size - 3
    out = []
    for i, it in enumerate(items or []):
        if isinstance(it, str):
            it = {"text": it}
        out.append(para(it["text"], size, bullet=BULLET_CHAR[0], level=0,
                        space_before=gap if i else 0))
        for s in it.get("sub", []) or []:
            out.append(para(s, sub_size, color=THEME["muted"], bullet=BULLET_CHAR[1],
                            level=1, space_before=3))
    return out


def header(slide, meta, page_no, total):
    els = [
        rect(0, 0, SLIDE_W, SLIDE_H, fill=THEME["white"]),
        rect(0.36, 0.42, 0.09, 0.62, fill=THEME["accent"]),
        text_el(MARGIN_X, 0.30, SLIDE_W - 2 * MARGIN_X, 0.86,
                [para(slide.get("title", ""), 28, color=THEME["primary"], bold=True)],
                valign="m", min_scale=0.7),
        rect(MARGIN_X, 1.2, SLIDE_W - 2 * MARGIN_X, 0.02, fill=THEME["line"]),
    ]
    foot = " · ".join(v for v in [meta.get("short_name"), meta.get("venue")] if v)
    els.append(text_el(MARGIN_X, FOOTER_Y, 8, 0.3, [para(foot, 10, color=THEME["muted"])], valign="m"))
    els.append(text_el(SLIDE_W - MARGIN_X - 2, FOOTER_Y, 2, 0.3,
                       [para(f"{page_no} / {total}", 10, color=THEME["muted"], align="r")], valign="m"))
    if slide.get("section"):
        els.append(text_el(SLIDE_W - MARGIN_X - 4, 0.08, 4, 0.26,
                           [para(slide["section"], 10, color=THEME["muted"], align="r")], valign="m"))
    return els


def takeaway_block(text, bottom=CONTENT_BOTTOM):
    """底部结论框。返回 (元素列表, 结论框顶部 y)。"""
    if not text:
        return [], bottom
    w = SLIDE_W - 2 * MARGIN_X
    paras = [para(text, 17, color=THEME["primary"], bold=True)]
    inner_w = w - 0.55
    h_text = measure_paras(paras, inner_w)
    h = max(0.62, min(1.2, h_text + 0.24))
    y = bottom - h
    els = [rect(MARGIN_X, y, w, h, fill=THEME["warm"]),
           rect(MARGIN_X, y, 0.08, h, fill=THEME["highlight"]),
           text_el(MARGIN_X + 0.32, y + 0.08, inner_w, h - 0.16, paras, valign="m")]
    return els, y - 0.2


def figure_block(fig, x, y, w, h, caption=None):
    """图片 + 下方图注。caption 为 None 时用原文标签。"""
    cap = caption if caption is not None else fig.get("label", "")
    els = []
    cap_h = 0.0
    if cap:
        cap_paras = [para(cap, 12, color=THEME["muted"], align="c", italic=False)]
        cap_h = min(0.7, measure_paras(cap_paras, w) + 0.06)
    img_h = h - cap_h - (0.06 if cap else 0)
    img = image_el(fig["file"], x, y, w, img_h)
    els.append(rect(img["x"] - 0.06, img["y"] - 0.06, img["w"] + 0.12, img["h"] + 0.12,
                    fill=THEME["white"], line=THEME["line"], line_w=0.75))
    els.append(img)
    if cap:
        els.append(text_el(x, img["y"] + img["h"] + 0.1, w, cap_h, cap_paras))
    return els


# ================================================================ 各页型
def layout_cover(s, meta, figs, cpath, n, total):
    els = [rect(0, 0, SLIDE_W, SLIDE_H, fill=THEME["primary"]),
           rect(0, 5.25, SLIDE_W, 0.06, fill=THEME["highlight"]),
           rect(0.9, 1.25, 0.12, 2.6, fill=THEME["accent"])]
    title = s.get("title") or meta.get("title", "")
    sub = s.get("subtitle") or meta.get("title_zh", "")
    tparas = [para(title, 36, color=THEME["white"], bold=True)]
    if sub:
        tparas.append(para(sub, 24, color="D6E4F0", space_before=14))
    els.append(text_el(1.3, 1.1, 11.2, 2.9, tparas, valign="m", min_scale=0.6))
    info = []
    if meta.get("authors"):
        info.append(para(meta["authors"], 16, color="C9D6E3"))
    venue = " · ".join(v for v in [meta.get("venue"), str(meta.get("year") or "")] if v)
    if venue:
        info.append(para(venue, 16, color="C9D6E3", space_before=6))
    els.append(text_el(1.3, 4.1, 11.2, 1.05, info, valign="t", min_scale=0.7))
    bottom = " · ".join(v for v in [s.get("presenter") or meta.get("presenter"),
                                    s.get("date") or meta.get("date")] if v)
    bottom = bottom or "论文精读"
    els.append(text_el(1.3, 5.6, 11.2, 0.5, [para(bottom, 16, color=THEME["white"])], valign="m"))
    return els


def layout_section(s, meta, figs, cpath, n, total):
    els = [rect(0, 0, SLIDE_W, SLIDE_H, fill=THEME["primary"]),
           rect(1.0, 3.55, 1.4, 0.07, fill=THEME["highlight"])]
    els.append(text_el(1.0, 2.2, 11.3, 1.2, [para(s.get("title", ""), 36, color=THEME["white"], bold=True)],
                       valign="b"))
    if s.get("subtitle"):
        els.append(text_el(1.0, 3.85, 11.3, 1.2, [para(s["subtitle"], 20, color="D6E4F0")]))
    return els


def layout_agenda(s, meta, figs, cpath, n, total):
    els = header(s, meta, n, total)
    items = s.get("items") or []
    if not items:
        raise SlideError("agenda 页缺少 items")
    k = len(items)
    top, bottom = CONTENT_TOP + 0.15, CONTENT_BOTTOM
    gap = 0.18
    h = min(0.95, (bottom - top - gap * (k - 1)) / k)
    w = SLIDE_W - 2 * MARGIN_X - 1.0
    x = MARGIN_X + 0.5
    y = top + ((bottom - top) - (h * k + gap * (k - 1))) / 2
    for i, it in enumerate(items):
        txt = it if isinstance(it, str) else it.get("text", "")
        desc = "" if isinstance(it, str) else it.get("desc", "")
        els.append(rect(x, y, w, h, fill=THEME["soft"]))
        els.append(rect(x, y, h, h, fill=THEME["accent"]))
        els.append(text_el(x, y, h, h, [para(f"{i + 1:02d}", 22, color=THEME["white"], bold=True, align="c")],
                           valign="m"))
        ps = [para(txt, 20, color=THEME["primary"], bold=True)]
        if desc:
            ps.append(para(desc, 14, color=THEME["muted"], space_before=2))
        els.append(text_el(x + h + 0.3, y + 0.04, w - h - 0.5, h - 0.08, ps, valign="m"))
        y += h + gap
    return els


def layout_bullets(s, meta, figs, cpath, n, total):
    els = header(s, meta, n, total)
    tk, bottom = takeaway_block(s.get("takeaway"))
    if not s.get("bullets"):
        raise SlideError("bullets 页缺少 bullets")
    paras = bullet_paras(s["bullets"], size=s.get("font_size", 21))
    w = SLIDE_W - 2 * MARGIN_X - 0.3
    els.append(text_el(MARGIN_X + 0.15, CONTENT_TOP + 0.1, w, bottom - CONTENT_TOP - 0.1, paras,
                       valign="t" if len(paras) > 3 else "m"))
    return els + tk


def _need_fig(s, figs, cpath, key="figure"):
    ref = s.get(key)
    try:
        fig = resolve_figure(ref, figs, cpath, s.get("crop"))
    except ValueError as ex:
        raise SlideError(str(ex))
    if fig is None:
        raise SlideError(f"找不到图片引用 {ref!r}（应为 figures.json 里的 id 或相对路径）")
    return fig


def layout_figure(s, meta, figs, cpath, n, total):
    """单图页。有 bullets 时按图的宽高比自动选择上下或左右布局。"""
    els = header(s, meta, n, total)
    tk, bottom = takeaway_block(s.get("takeaway"))
    fig = _need_fig(s, figs, cpath)
    caption = s.get("caption")
    top = CONTENT_TOP
    W = SLIDE_W - 2 * MARGIN_X
    H = bottom - top
    bullets = s.get("bullets") or []
    if not bullets:
        els += figure_block(fig, MARGIN_X, top, W, H, caption)
        return els + tk
    ar = image_aspect(fig["file"])
    pos = s.get("layout") or ("top" if ar >= 2.2 else "left")
    if pos == "top":
        paras = bullet_paras(bullets, size=18, gap=6)
        text_h = min(H * 0.45, measure_paras(paras, W - 0.3) + 0.1)
        els += figure_block(fig, MARGIN_X, top, W, H - text_h - 0.15, caption)
        els.append(text_el(MARGIN_X + 0.15, bottom - text_h, W - 0.3, text_h, paras))
    else:
        # 左文右图；图越宽，留给图的比例越大
        fig_frac = 0.58 if ar > 1.1 else 0.5
        fw = W * fig_frac
        tw = W - fw - 0.4
        paras = bullet_paras(bullets, size=19, gap=10)
        els.append(text_el(MARGIN_X, top + 0.1, tw, H - 0.1, paras, valign="m"))
        els += figure_block(fig, MARGIN_X + tw + 0.4, top, fw, H, caption)
    return els + tk


def layout_two_figures(s, meta, figs, cpath, n, total):
    els = header(s, meta, n, total)
    tk, bottom = takeaway_block(s.get("takeaway"))
    items = s.get("figures") or []
    if len(items) != 2:
        raise SlideError("two_figures 页需要恰好 2 个 figures")
    W = SLIDE_W - 2 * MARGIN_X
    H = bottom - CONTENT_TOP
    resolved = []
    for it in items:
        try:
            fig = resolve_figure(it.get("ref"), figs, cpath, it.get("crop"))
        except ValueError as ex:
            raise SlideError(str(ex))
        if fig is None:
            raise SlideError(f"找不到图片引用 {it.get('ref')!r}")
        resolved.append((fig, it.get("caption")))
    ars = [image_aspect(f["file"]) for f, _ in resolved]
    stacked = s.get("layout") == "stack" or (s.get("layout") != "side" and min(ars) >= 2.4)
    if stacked:
        h = (H - 0.25) / 2
        for i, (fig, cap) in enumerate(resolved):
            els += figure_block(fig, MARGIN_X, CONTENT_TOP + i * (h + 0.25), W, h, cap)
    else:
        w = (W - 0.4) / 2
        for i, (fig, cap) in enumerate(resolved):
            els += figure_block(fig, MARGIN_X + i * (w + 0.4), CONTENT_TOP, w, H, cap)
    return els + tk


def table_el(x, y, w, header_row, rows, col_w=None, size=18, max_h=4.5, min_size=11):
    ncol = len(header_row)
    for r in rows:
        if len(r) != ncol:
            raise SlideError(f"表格行列数不一致：表头 {ncol} 列，某行 {len(r)} 列：{r}")
    if col_w:
        if len(col_w) != ncol:
            raise SlideError("col_widths 数量与表头列数不一致")
        tot = float(sum(col_w))
        cw = [w * c / tot for c in col_w]
    else:
        # 按内容长度分配列宽
        lens = [max(text_width(plain(str(r[i])), 12) for r in [header_row] + rows) for i in range(ncol)]
        lens = [min(max(l, 0.8), 4.0) for l in lens]
        tot = sum(lens)
        cw = [w * l / tot for l in lens]
    pad_x, pad_y = 0.12, 0.09
    sz = size
    while True:
        fitted, heights = [], []
        for ri, r in enumerate([header_row] + rows):
            cells, hmax = [], 0
            for ci, c in enumerate(r):
                runs = parse_rich(str(c))
                if ri == 0:
                    runs = [(t, True) for t, _ in runs]
                lines = wrap_runs(runs, sz, cw[ci] - 2 * pad_x)
                hh = line_height(sz, 1.0) * len(lines) + 2 * pad_y
                hmax = max(hmax, hh)
                cells.append(lines)
            fitted.append(cells)
            heights.append(max(hmax, line_height(sz, 1.0) + 2 * pad_y + 0.08))
        if sum(heights) <= max_h or sz <= min_size:
            break
        sz -= 0.5
    return {"kind": "table", "x": x, "y": y, "w": w, "col_w": cw, "row_h": heights,
            "h": sum(heights), "size": sz, "cells": fitted, "pad_x": pad_x, "pad_y": pad_y,
            "header": [str(c) for c in header_row], "rows": [[str(c) for c in r] for r in rows],
            "overflow": sum(heights) > max_h + 0.02}


def layout_table(s, meta, figs, cpath, n, total):
    els = header(s, meta, n, total)
    tk, bottom = takeaway_block(s.get("takeaway"))
    hdr, rows = s.get("header"), s.get("rows")
    if not hdr or not rows:
        raise SlideError("table 页需要 header 和 rows")
    W = SLIDE_W - 2 * MARGIN_X
    note_paras = [para(s["note"], 14, color=THEME["muted"])] if s.get("note") else []
    note_h = measure_paras(note_paras, W) + 0.1 if note_paras else 0
    max_h = bottom - CONTENT_TOP - 0.1 - note_h
    t = table_el(MARGIN_X, CONTENT_TOP + 0.1, W, hdr, rows, s.get("col_widths"),
                 size=s.get("font_size", 18), max_h=max_h)
    # 表格较矮时垂直居中
    free = max_h - t["h"]
    if free > 0.4:
        t["y"] += free * 0.35
    els.append(t)
    if note_paras:
        els.append(text_el(MARGIN_X, t["y"] + t["h"] + 0.1, W, note_h, note_paras))
    return els + tk


def layout_metrics(s, meta, figs, cpath, n, total):
    """关键数字卡片：2–4 张。"""
    els = header(s, meta, n, total)
    tk, bottom = takeaway_block(s.get("takeaway"))
    ms = s.get("metrics") or []
    if not 1 <= len(ms) <= 4:
        raise SlideError("metrics 页需要 1–4 个 metrics")
    W = SLIDE_W - 2 * MARGIN_X
    bullets = s.get("bullets") or []
    k = len(ms)
    gap = 0.3
    cw = (W - gap * (k - 1)) / k
    ch = 2.35 if bullets else min(3.2, bottom - CONTENT_TOP - 0.3)
    y = CONTENT_TOP + 0.2
    for i, m in enumerate(ms):
        x = MARGIN_X + i * (cw + gap)
        els.append(rect(x, y, cw, ch, fill=THEME["soft"]))
        els.append(rect(x, y, cw, 0.07, fill=THEME["accent"]))
        els.append(text_el(x + 0.15, y + 0.2, cw - 0.3, 0.95,
                           [para(m.get("value", ""), 40, color=THEME["accent"], bold=True, align="c")],
                           valign="m", min_scale=0.5))
        ps = [para(m.get("label", ""), 17, color=THEME["primary"], bold=True, align="c")]
        if m.get("desc"):
            ps.append(para(m["desc"], 13, color=THEME["muted"], align="c", space_before=4))
        els.append(text_el(x + 0.15, y + 1.2, cw - 0.3, ch - 1.3, ps, valign="t"))
    if bullets:
        ty = y + ch + 0.3
        els.append(text_el(MARGIN_X + 0.15, ty, W - 0.3, bottom - ty, bullet_paras(bullets, size=18, gap=6)))
    return els + tk


def layout_end(s, meta, figs, cpath, n, total):
    els = [rect(0, 0, SLIDE_W, SLIDE_H, fill=THEME["primary"]),
           rect(1.0, 1.55, 1.4, 0.07, fill=THEME["highlight"])]
    els.append(text_el(1.0, 0.6, 11.3, 0.9, [para(s.get("title") or "谢谢", 36, color=THEME["white"], bold=True)],
                       valign="b"))
    if s.get("bullets"):
        ps = bullet_paras(s["bullets"], size=20, gap=12)
        for p in ps:
            p["color"] = THEME["white"] if p["level"] == 0 else "C9D6E3"
        els.append(text_el(1.0, 1.95, 11.3, 4.4, ps, valign="t"))
    if s.get("subtitle"):
        els.append(text_el(1.0, 6.4, 11.3, 0.5, [para(s["subtitle"], 16, color="C9D6E3")], valign="m"))
    return els


LAYOUTS = {
    "cover": layout_cover,
    "section": layout_section,
    "agenda": layout_agenda,
    "bullets": layout_bullets,
    "figure": layout_figure,
    "two_figures": layout_two_figures,
    "table": layout_table,
    "metrics": layout_metrics,
    "end": layout_end,
}


def layout_slide(slide, meta, figs, content_path, page_no, total):
    t = slide.get("type", "bullets")
    fn = LAYOUTS.get(t)
    if fn is None:
        raise SlideError(f"未知页型 {t!r}，可选：{', '.join(LAYOUTS)}")
    return fn(slide, meta, figs, content_path, page_no, total)


def overflow_report(elements) -> list[str]:
    msgs = []
    for e in elements:
        if e.get("overflow"):
            if e["kind"] == "text":
                first = plain(e["paras"][0]["text"])[:24] if e["paras"] else ""
                msgs.append(f"文本溢出（已缩到 {e['scale']:.0%} 仍放不下）：“{first}…”，"
                            f"需要 {e['content_h']:.2f}in，可用 {e['h']:.2f}in")
            else:
                msgs.append(f"表格溢出：需要 {e['h']:.2f}in")
    return msgs
