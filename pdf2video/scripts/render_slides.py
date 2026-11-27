#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 pdf2ppt 的成品 pptx 渲成视频底图，并导出每页槽位几何。

视频渲染需要两样东西：
  1. slides/slide-NN.png —— 高分辨率底图（默认 2x 超采样，3840x2160），
     放大（zoom）时从 4K 底图裁剪，1080p 成片依然清晰；
  2. slots.json —— 每页的区域几何（标题/正文/图槽/总结，1080p 像素坐标），
     光标移动、高亮、局部放大都按它定位。

渲染复用 pdf2ppt 的 preview_slides.py：机器上有 LibreOffice 走 soffice，
没有走 Pillow 逐形状近似绘制（两条路都够视频用，视频成片就是这份底图）。

用法：
    python3 render_slides.py --pptx <论文题目>-slide.pptx --out <视频工作目录>
    python3 render_slides.py --pptx x.pptx --out . --base-scale 2.0
"""

import argparse
import json
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
# 复用 pdf2ppt 的渲染器（本技能的前置），环境变量可覆盖
PDF2PPT_SCRIPTS = os.environ.get(
    "PDF2PPT_SCRIPTS", os.path.join(SKILL_DIR, "..", "pdf2ppt", "scripts"))
sys.path.insert(0, os.path.abspath(PDF2PPT_SCRIPTS))

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
EMU = 360000.0
SLIDE_W_CM, SLIDE_H_CM = 33.87, 19.05

# 已知文本槽（名称取自 pdf2ppt 模板；见 pdf2ppt/scripts/build_ppt.py KIND_SLOTS）
TEXT_SLOT_NAMES = {
    "文本框 1": "title",      # 内容页标题
    "标题 1": "title",        # 封面标题
    "文本框 31": "body",      # 正文要点
    "文本框 33": "summary",   # 底部总结
    "副标题 2": "subtitle",   # 封面副标题
    "文本框 3": "presenter",  # 封面汇报人
    "文本框 6": "takeaway",   # closing 的 Take-away 句
}


def render_base_pngs(pptx, out_dir, dpi):
    """调 pdf2ppt 的 preview_slides 出整册底图（soffice 优先，Pillow 兜底）。"""
    try:
        import preview_slides
    except ImportError:
        raise SystemExit("找不到 pdf2ppt/scripts/preview_slides.py：%s\n"
                         "pdf2video 以 pdf2ppt 为前置技能，两个技能须在同一 skills 目录下"
                         % PDF2PPT_SCRIPTS)
    pngs = preview_slides.via_soffice(pptx, out_dir, dpi=dpi)
    mode = "soffice"
    if not pngs:
        pngs = preview_slides.via_pillow(pptx, out_dir, dpi=dpi)
        mode = "pillow"
    if not pngs:
        raise SystemExit("底图渲染失败：既没有 soffice，Pillow 路线也报错")
    # 统一命名成 slide-NN.png 并缩放成严格 16:9 的 2x 分辨率
    from PIL import Image
    tw, th = int(round(SLIDE_W_CM * dpi / 2.54)), int(round(SLIDE_H_CM * dpi / 2.54))
    out = []
    for i, p in enumerate(sorted(pngs), 1):
        im = Image.open(p).convert("RGB")
        if im.size != (tw, th):
            im = im.resize((tw, th), Image.LANCZOS)
        dst = os.path.join(out_dir, "slide-%02d.png" % i)
        im.save(dst)
        if dst != p:
            os.remove(p)
        out.append(dst)
    return out, mode, (tw, th)


def parse_shapes(pptx, slide_no):
    """读一页 slide XML 里所有带几何的形状（EMU）。"""
    with zipfile.ZipFile(pptx) as z:
        root = ET.fromstring(z.read("ppt/slides/slide%d.xml" % slide_no))
    tree = root.find("./" + P + "cSld/" + P + "spTree")
    shapes = []
    if tree is None:
        return shapes
    for sp in tree.iter():
        tag = sp.tag
        if tag not in (P + "sp", P + "pic", P + "cxnSp"):
            continue
        xf = sp.find(".//" + A + "xfrm")
        if xf is None:
            continue
        off, ext = xf.find(A + "off"), xf.find(A + "ext")
        if off is None or ext is None:
            continue
        try:
            box = [int(off.get("x")) / EMU, int(off.get("y")) / EMU,
                   int(ext.get("cx")) / EMU, int(ext.get("cy")) / EMU]
        except (TypeError, ValueError):
            continue
        name = ""
        nv = sp.find(".//" + P + "cNvPr")
        if nv is not None:
            name = nv.get("name") or ""
        kind = "pic" if tag == P + "pic" else ("line" if tag == P + "cxnSp" else "shape")
        shapes.append({"name": name, "kind": kind, "cm": box})
    return shapes


def cluster_figures(items, n_target):
    """把散落的图形状（图片+自绘画布）聚成 n_target 个图区域。

    多图版式都是上下堆叠（单槽切分或 status 双槽）。直接用 content.json 给的
    图数当目标簇数：按 y 排序后在最大的 (n-1) 个竖向间隙处切开——既保证
    双图不粘成一团，也不会把单幅图内部的形状拆散。
    """
    if not items:
        return []
    boxes = [it["cm"] for it in items]
    order = sorted(range(len(boxes)), key=lambda i: (boxes[i][1], boxes[i][0]))
    sb = [boxes[i] for i in order]
    n_target = max(1, min(n_target, len(sb)))
    if n_target == 1:
        groups = [sb]
    else:
        gaps = []
        for i in range(len(sb) - 1):
            gap = sb[i + 1][1] - (sb[i][1] + sb[i][3])
            gaps.append((max(0.0, gap), i + 1))
        cuts = sorted(i for _, i in sorted(gaps, reverse=True)[:n_target - 1])
        groups, prev = [], 0
        for c in cuts + [len(sb)]:
            groups.append(sb[prev:c])
            prev = c
    out = []
    for g in groups:
        x0 = min(b[0] for b in g); y0 = min(b[1] for b in g)
        x1 = max(b[0] + b[2] for b in g); y1 = max(b[1] + b[3] for b in g)
        out.append({"x": x0, "y": y0, "cx": x1 - x0, "cy": y1 - y0})
    return out


def fig_count(page):
    f = page.get("figure")
    if isinstance(f, list):
        return len([x for x in f if x])
    return 1 if f else 0


def extract_slots(pptx, kinds, fig_counts, px_w, px_h):
    """每页区域几何（1080p 像素坐标）：title/body/summary/figures。"""
    n = len(kinds)
    sx, sy = px_w / SLIDE_W_CM, px_h / SLIDE_H_CM
    pages = []
    for no in range(1, n + 1):
        kind = kinds[no - 1]
        shapes = parse_shapes(pptx, no)
        regs = {"title": None, "body": None, "summary": None, "figures": [],
                "subtitle": None, "presenter": None, "takeaway": None}
        fig_items = []
        for sh in shapes:
            slot = TEXT_SLOT_NAMES.get(sh["name"])
            if slot:
                regs[slot] = sh["cm"]
            elif sh["kind"] in ("pic", "shape", "line"):
                fig_items.append(sh)
        # 自绘图形都是整幅图的组成部分；把线框也算进图区域
        regs["figures"] = [[f["x"], f["y"], f["cx"], f["cy"]]
                           for f in cluster_figures(fig_items, fig_counts[no - 1])]
        pages.append({"page": no, "kind": kind, "regions": regs})

    # 封面/closing 的兜底区域：缺 title/body 时给整页安全带，光标有处可去
    for pg in pages:
        k, r = pg["kind"], pg["regions"]
        union_txt = [x for x in (r["title"], r["body"], r["summary"],
                                 r["subtitle"], r["presenter"], r["takeaway"]) if x]
        if k in ("cover", "closing") and not r["figures"]:
            r["figures"] = []
        if k == "closing" and r["takeaway"] and not r["summary"]:
            r["summary"] = r["takeaway"]
        if k == "cover" and r["subtitle"] and not r["body"]:
            r["body"] = r["subtitle"]
        if not r["title"] and union_txt:
            r["title"] = union_txt[0]
        if not r["body"] and len(union_txt) > 1:
            r["body"] = union_txt[1]

    def to_px(b):
        if not b:
            return None
        return [round(b[0] * sx), round(b[1] * sy), round(b[2] * sx), round(b[3] * sy)]

    for pg in pages:
        r = pg["regions"]
        r["title"], r["body"], r["summary"] = to_px(r["title"]), to_px(r["body"]), to_px(r["summary"])
        r["subtitle"], r["presenter"], r["takeaway"] = to_px(r["subtitle"]), to_px(r["presenter"]), to_px(r["takeaway"])
        r["figures"] = [to_px(f) for f in r["figures"]]
    return pages


def main():
    ap = argparse.ArgumentParser(description="pptx→视频底图+槽位几何")
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--out", required=True, help="视频工作目录")
    ap.add_argument("--base-scale", type=float, default=2.0,
                    help="底图超采样倍率（2.0=3840x2160），放大时保清晰")
    ap.add_argument("--content", help="content.json（读页面 kind；缺省按形状名猜 kind）")
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out)
    slides_dir = os.path.join(out_dir, "slides")
    os.makedirs(slides_dir, exist_ok=True)

    dpi = 1080.0 * args.base_scale / SLIDE_H_CM * 2.54  # 1080p 成片 × scale
    pngs, mode, (tw, th) = render_base_pngs(args.pptx, slides_dir, dpi)
    print("底图：%d 页，%dx%d（%s 路线，dpi=%d）" % (len(pngs), tw, th, mode, round(dpi)))

    kinds, fig_counts = None, None
    if args.content and os.path.exists(args.content):
        cpages = json.load(open(args.content))["pages"]
        kinds = [p.get("kind", "text-left") for p in cpages]
        fig_counts = [fig_count(p) for p in cpages]
    else:
        fig_counts = [1] * len(pngs)
        # 从 pptx 尺寸猜：有副标题文本框的是封面，含文本框 33 的是正文/总结页
        kinds = []
        for no in range(1, len(pngs) + 1):
            names = {s["name"] for s in parse_shapes(args.pptx, no)}
            if "标题 1" in names:
                kinds.append("cover")
            elif "文本框 6" in names:
                kinds.append("closing")
            else:
                kinds.append("text-left")
    pages = extract_slots(args.pptx, kinds, fig_counts, tw, th)

    # 视频端一律用 1080p 坐标；slots.json 存的是底图坐标，另存 1080p 等比换算
    k = (1920.0 / tw, 1080.0 / th)
    for pg in pages:
        r = pg["regions"]
        def sc(b):
            if not b:
                return None
            return [round(b[0] * k[0]), round(b[1] * k[1]), round(b[2] * k[0]), round(b[3] * k[1])]
        r["title"], r["body"], r["summary"] = sc(r["title"]), sc(r["body"]), sc(r["summary"])
        r["subtitle"], r["presenter"], r["takeaway"] = sc(r["subtitle"]), sc(r["presenter"]), sc(r["takeaway"])
        r["figures"] = [sc(f) for f in r["figures"]]

    slots = {"base": [tw, th], "video": [1920, 1080], "render_mode": mode,
             "slides": [os.path.basename(p) for p in pngs], "pages": pages}
    path = os.path.join(out_dir, "slots.json")
    json.dump(slots, open(path, "w"), ensure_ascii=False, indent=1)
    print("槽位几何：%s" % path)
    for pg in pages[:3]:
        r = pg["regions"]
        print("  第%d页 %-12s 图区%d个 body=%s" % (
            pg["page"], pg["kind"], len(r["figures"]),
            "有" if r["body"] else "无"))


if __name__ == "__main__":
    main()
