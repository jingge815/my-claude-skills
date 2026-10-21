#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把生成好的 pptx 逐页渲成 PNG，用来肉眼核对版式。

脚本的几何校验查得出「框压框、字出框」，查不出「这一页看着好不好」。
交付前必须看图：图小到读不清、图文挤在一起、一边满一边空，都要在这里发现。

两条路，自动选：
  1. 有 LibreOffice（soffice）→ 转 PDF 再转 PNG，最接近 PowerPoint 的实际效果；
  2. 没有 → 用 Pillow 按形状几何近似画一遍（字体、换行与 PowerPoint 有差异，
     只用来看布局，不用来判断字号是否恰好放得下）。

用法：
    python3 preview_slides.py --pptx 答辩PPT.pptx --out-dir work/preview
    python3 preview_slides.py --pptx 答辩PPT.pptx --out-dir work/preview --pages 2,5,6
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import zipfile
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
EMU = 360000.0
SLIDE_W, SLIDE_H = 33.87, 19.05

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/kaiti/KaiTi.ttf",
    "/usr/share/fonts/truetype/arphic/ukai.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/simkai.ttf",
]


# ------------------------------------------------------------------ 路线 1
def via_soffice(pptx, out_dir, dpi=110):
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    work = os.path.join(out_dir, "_pdf")
    os.makedirs(work, exist_ok=True)
    try:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf",
                        "--outdir", work, os.path.abspath(pptx)],
                       check=True, capture_output=True, timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print("  soffice 转换失败：%s" % e, file=sys.stderr)
        return None
    pdfs = glob.glob(os.path.join(work, "*.pdf"))
    if not pdfs:
        return None
    pdf = pdfs[0]
    if shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-png", "-r", str(dpi), pdf,
                        os.path.join(out_dir, "slide")],
                       check=False, capture_output=True)
        pngs = sorted(glob.glob(os.path.join(out_dir, "slide-*.png")))
        if pngs:
            shutil.rmtree(work, ignore_errors=True)
            return pngs
    print("  有 soffice 但没有 pdftoppm：PDF 在 %s，可直接看" % pdf, file=sys.stderr)
    return None


# ------------------------------------------------------------------ 路线 2
def _font(path, pt, dpi, cache):
    from PIL import ImageFont
    key = round(pt, 1)
    if key not in cache:
        px = max(6, int(round(pt / 72.0 * dpi)))
        try:
            cache[key] = ImageFont.truetype(path, px) if path else ImageFont.load_default()
        except Exception:
            cache[key] = ImageFont.load_default()
    return cache[key]


def _rgb(el):
    """从 solidFill 里取 srgbClr。取不到返回 None。"""
    if el is None:
        return None
    clr = el.find(A + "srgbClr")
    if clr is not None and clr.get("val"):
        return "#" + clr.get("val")
    return None


def _shape_fill(sp):
    spPr = sp.find("./" + P + "spPr")
    if spPr is None:
        return None, None
    if spPr.find(A + "noFill") is not None:
        fill = None
    else:
        fill = _rgb(spPr.find(A + "solidFill"))
    ln = spPr.find(A + "ln")
    line = None
    if ln is not None and ln.find(A + "noFill") is None:
        line = _rgb(ln.find(A + "solidFill"))
    return fill, line


def via_pillow(pptx, out_dir, dpi=100):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("  没有 Pillow，无法出近似预览", file=sys.stderr)
        return None
    font_path = next((c for c in FONT_CANDIDATES if os.path.exists(c)), None)
    scale = dpi / 2.54
    W, H = int(SLIDE_W * scale), int(SLIDE_H * scale)
    cache = {}
    out = []

    with zipfile.ZipFile(pptx) as z:
        names = sorted((n for n in z.namelist()
                        if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                       key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        media = {}
        for n in z.namelist():
            if n.startswith("ppt/media/"):
                media[os.path.basename(n)] = z.read(n)

        for i, name in enumerate(names, 1):
            root = ET.fromstring(z.read(name))
            tree = root.find("./" + P + "cSld/" + P + "spTree")
            img = Image.new("RGB", (W, H), "#F2F2F2")
            d = ImageDraw.Draw(img)
            # 版式给的深色标题条，近似画一条，位置与模板一致
            d.rectangle([0, 0, W, int(2.17 * scale)], fill="#1F4E8C")
            rels = {}
            rel_name = "ppt/slides/_rels/%s.rels" % os.path.basename(name)
            try:
                rel_root = ET.fromstring(z.read(rel_name))
                for rel in rel_root:
                    tgt = rel.get("Target") or ""
                    if "media/" in tgt:
                        rels[rel.get("Id")] = os.path.basename(tgt)
            except KeyError:
                pass

            if tree is not None:
                for sp in list(tree):
                    tag = sp.tag
                    if tag not in (P + "sp", P + "pic"):
                        continue
                    xf = sp.find(".//" + A + "xfrm")
                    if xf is None:
                        continue
                    off, ext = xf.find(A + "off"), xf.find(A + "ext")
                    if off is None or ext is None:
                        continue
                    x = int(off.get("x")) / EMU * scale
                    y = int(off.get("y")) / EMU * scale
                    w = int(ext.get("cx")) / EMU * scale
                    h = int(ext.get("cy")) / EMU * scale

                    if tag == P + "pic":
                        blip = sp.find(".//" + A + "blip")
                        rid = blip.get(R + "embed") if blip is not None else None
                        fname = rels.get(rid)
                        if fname and fname in media:
                            try:
                                from io import BytesIO
                                im = Image.open(BytesIO(media[fname])).convert("RGBA")
                                im = im.resize((max(1, int(w)), max(1, int(h))),
                                               Image.LANCZOS)
                                img.paste(im, (int(x), int(y)), im)
                                continue
                            except Exception:
                                pass
                        d.rectangle([x, y, x + w, y + h], outline="#999999")
                        continue

                    fill, line = _shape_fill(sp)
                    # prst="pie" 的扇区按扇形画：按矩形铺满会让最后一个扇区的
                    # 颜色盖住整块，饼图在预览里变成一个纯色方块。
                    prst = sp.find(".//" + A + "prstGeom")
                    prst_name = prst.get("prst") if prst is not None else None
                    if prst_name == "pie":
                        adj = {g.get("name"): g.get("fmla", "")
                               for g in sp.findall(".//" + A + "gd")}

                        def _deg(key, default):
                            raw = adj.get(key, "")
                            try:
                                return float(raw.split("val")[-1].strip()) / 60000.0
                            except (TypeError, ValueError):
                                return default

                        a0, a1 = _deg("adj1", -90.0), _deg("adj2", 270.0)
                        if fill:
                            d.pieslice([x, y, x + w, y + h], a0, a1,
                                       fill=fill, outline="white", width=2)
                        continue
                    if fill:
                        d.rectangle([x, y, x + w, y + h], fill=fill,
                                    outline=line or fill)
                    elif line:
                        d.rectangle([x, y, x + w, y + h], outline=line)

                    body = sp.find("./" + P + "txBody")
                    if body is None:
                        continue
                    cy = y + 0.08 * scale
                    for para in body.findall(A + "p"):
                        runs = para.findall(A + "r")
                        text = "".join("".join(t.text or "" for t in r.findall(A + "t"))
                                       for r in runs)
                        if not text.strip():
                            continue
                        pt = 18.0
                        color = "#202830"
                        for r in runs:
                            rpr = r.find(A + "rPr")
                            if rpr is not None:
                                if rpr.get("sz"):
                                    pt = int(rpr.get("sz")) / 100.0
                                c = _rgb(rpr.find(A + "solidFill"))
                                if c:
                                    color = c
                                break
                        ppr = para.find(A + "pPr")
                        marL = 0.0
                        algn = ""
                        if ppr is not None:
                            try:
                                marL = int(ppr.get("marL") or 0) / EMU * scale
                            except ValueError:
                                marL = 0.0
                            algn = ppr.get("algn") or ""
                        f = _font(font_path, pt, dpi, cache)
                        avail = max(10, w - marL - 0.1 * scale)
                        # 折行
                        lines, cur = [], ""
                        for ch in text:
                            if d.textlength(cur + ch, font=f) <= avail or not cur:
                                cur += ch
                            else:
                                lines.append(cur)
                                cur = ch
                        if cur:
                            lines.append(cur)
                        for ln in lines:
                            tw = d.textlength(ln, font=f)
                            if algn == "ctr":
                                tx = x + (w - tw) / 2
                            elif algn == "r":
                                tx = x + w - tw - 0.08 * scale
                            else:
                                tx = x + marL + 0.08 * scale
                            d.text((tx, cy), ln, font=f, fill=color)
                            cy += pt * 1.22 / 72.0 * dpi
            path = os.path.join(out_dir, "slide-%02d.png" % i)
            img.save(path)
            out.append(path)
    return out


def main():
    ap = argparse.ArgumentParser(description="把 pptx 逐页渲成 PNG 供肉眼核对")
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--pages", help="只保留这些页，如 2,5,6")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    for old in glob.glob(os.path.join(args.out_dir, "slide*.png")):
        os.remove(old)

    pngs = via_soffice(args.pptx, args.out_dir, args.dpi)
    mode = "soffice（接近 PowerPoint 实际效果）"
    if not pngs:
        pngs = via_pillow(args.pptx, args.out_dir, args.dpi)
        mode = "Pillow 近似（字体与换行有差异，只看布局）"
    if not pngs:
        raise SystemExit("没能出预览：装 LibreOffice，或装 Pillow 走近似预览")

    if args.pages:
        keep = {int(x) for x in re.split(r"[,\s]+", args.pages) if x.strip().isdigit()}
        for p in list(pngs):
            m = re.search(r"(\d+)\.png$", p)
            if m and int(m.group(1)) not in keep:
                os.remove(p)
                pngs.remove(p)

    print("预览方式：%s" % mode)
    print("已出 %d 页：%s" % (len(pngs), args.out_dir))
    for p in pngs:
        print("  %s" % p)
    print("\n逐页看一遍：图是否读得清、图文是否挤在一起、"
          "一页是否一边满一边空。没看过的页不算完成。")


if __name__ == "__main__":
    main()
