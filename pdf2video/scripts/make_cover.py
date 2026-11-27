#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按封面模板画 B 站风格封面（1920×1080），并做图文重叠检测。

模板固化在 config [cover]；吸睛内容来自 cover.json（由讲解代理根据 PPT 与
旁白写）：主标题（短促有力，可带高亮关键词）、标签 chips、论文原题、出处行。
右侧放论文首页截图（pdf2ppt 的 extract/cover.png，缺则用第 1 页底图）。

布局约束写死在代码里：左文右图两栏不相交，标题自动缩字号塞进左栏，
所有文本块与图片卡的包围盒做两两相交检测——「封面图文不重叠」是硬门禁。

产出：
    <out>/<题目>-cover.png      1920×1080（交付封面）
    cover/cover_2x.png          3840×2160（视频开场动画用底图，内部产物）

用法：
    python3 make_cover.py --content content.json --out <视频工作目录> \
            --extract ../<PDF名>_ppt --minutes 8
"""

import argparse
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cfgmod  # noqa: E402

W, H = 1920, 1080
CARD = (1140, 150, 1830, 830)        # 论文截图卡区域（右栏）
COL_L, COL_R = 84, CARD[0] - 46      # 左栏文字可用区
OVERLAP_MARGIN = 8                   # 相交检测余量


def font(path, size):
    return cfgmod.load_font(path, size)


def rounded(draw, box, r=None, **kw):
    if r is None:
        r = kw.pop("radius", 0)
    draw.rounded_rectangle(box, radius=r, **kw)


def text_w(draw, text, f):
    return draw.textlength(text, font=f)


import re as _re

_TOKEN_RE = _re.compile(r"[0-9A-Za-z!?+.%×!！?？]+|.")


def wrap_chars(draw, text, f, max_w):
    """中文贪心折行；连续 ASCII（数字/英文/×/! 等）作为原子词元不拆开。"""
    lines, cur = [], ""
    for tok in _TOKEN_RE.findall(text):
        if cur and text_w(draw, cur + tok, f) > max_w:
            lines.append(cur)
            cur = tok.lstrip()
        else:
            cur += tok
    if cur:
        lines.append(cur)
    return lines


def fit_title(draw, text, fpath, max_w, max_h, hi):
    """主标题自动缩字号：最多 3 行，塞进 (max_w, max_h)。返回
    (字号, lines, 高亮 [(行号, 行内起, 行内止), ...]——支持跨行关键词)。"""
    for size in range(138, 66, -8):
        f = font(fpath, size)
        lines = wrap_chars(draw, text, f, max_w)
        if len(lines) > 3:
            continue
        total_h = int(size * 1.24 * len(lines))
        if total_h > max_h:
            continue
        hits = []
        if hi and hi in text:
            s = text.find(hi)
            e = s + len(hi)
            g = 0
            for li, ln in enumerate(lines):
                ls, le = g, g + len(ln)
                g = le
                a, b = max(s, ls), min(e, le)
                if a < b:
                    hits.append((li, a - ls, b - ls))
        return size, lines, hits
    return 66, wrap_chars(draw, text, font(fpath, 66), max_w), []


def chip(draw, xy, text, f, pad_x, pad_y, fill, outline=None, text_fill=(26, 32, 44)):
    x, y = xy
    tw = text_w(draw, text, f)
    box = (x, y, x + tw + pad_x * 2, y + f.size + pad_y * 2)
    rounded(draw, box, radius=(box[3] - box[1]) // 2, fill=fill,
            outline=outline, width=2 if outline else 0)
    draw.text((x + pad_x, y + pad_y - f.size * 0.12), text, font=f, fill=text_fill)
    return box


def draw_bg(img, accent_rgb, bg_mode):
    """渐变底 + 光斑 + 点阵。"""
    import numpy as np
    top = (10, 22, 48) if bg_mode == "deep" else (243, 247, 252)
    bot = (18, 59, 110) if bg_mode == "deep" else (219, 231, 245)
    grad = np.linspace(0, 1, H)[:, None] * np.ones((1, W))
    arr = (np.array(top)[None, None, :] * (1 - grad[:, :, None])
           + np.array(bot)[None, None, :] * grad[:, :, None]).astype("uint8")
    img.paste(Image.fromarray(arr), (0, 0))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-260, -220, 620, 420), fill=accent_rgb + (36,))
    gd.ellipse((W - 640, H - 460, W + 320, H + 260), fill=(64, 132, 244, 40) if bg_mode == "deep" else (255, 213, 74, 46))
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    img.alpha_composite(glow)
    d = ImageDraw.Draw(img)
    for gy in range(60, H, 56):
        for gx in range(60, W, 56):
            d.ellipse((gx, gy, gx + 3, gy + 3),
                      fill=(255, 255, 255, 14) if bg_mode == "deep" else (30, 60, 100, 12))
    return d


def draw_card(img, pic, accent_rgb):
    """右栏论文截图卡：白边圆角卡 + 阴影，微旋转。返回旋转后的实际包围盒。"""
    pad = 18
    cw, ch = CARD[2] - CARD[0] - pad * 2, CARD[3] - CARD[1] - pad * 2
    inner = pic.copy().convert("RGB")
    iw, ih = inner.size
    s = min(cw / iw, ch / ih)
    inner = inner.resize((int(iw * s), int(ih * s)), Image.LANCZOS)
    card = Image.new("RGBA", (inner.width + pad * 2, inner.height + pad * 2), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    rounded(cd, (0, 0, card.width - 1, card.height - 1), 26, fill=(255, 255, 255, 255))
    rounded(cd, (pad, pad, pad + inner.width - 1, pad + inner.height - 1), 16, fill=(0, 0, 0, 255))
    card.paste(inner, (pad, pad))
    card = card.rotate(-2.5, expand=True, resample=Image.BICUBIC)
    # 阴影层
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    rounded(sd, (CARD[0] + 14, CARD[1] + 26, CARD[0] + 14 + card.width, CARD[1] + 26 + card.height),
            30, fill=(0, 0, 0, 130))
    sh = sh.filter(ImageFilter.GaussianBlur(26))
    img.alpha_composite(sh)
    img.alpha_composite(card, (CARD[0], CARD[1]))
    # 卡上角小标签
    d = ImageDraw.Draw(img)
    f = font(cfgmod.font_path(), 26)
    tag = "论文原文"
    tw = text_w(d, tag, f)
    box = (CARD[2] - tw - 56, CARD[1] - 6, CARD[2] + 6, CARD[1] + 48)
    rounded(d, box, 12, fill=accent_rgb + (255,))
    d.text((box[0] + 28, box[1] + 8), tag, font=f, fill=(26, 26, 26))
    return (CARD[0] - 8, CARD[1] - 54, CARD[0] + card.width + 8, CARD[1] + card.height + 8), d


def render(content, cover_meta, cfg, minutes, out_png, out_png_2x, card_src):
    accent = cfg["cover"]["accent"]
    accent_rgb = tuple(int(accent[i:i + 2], 16) for i in (1, 3, 5))
    bg_mode = cfg["cover"].get("bg", "deep")

    img = Image.new("RGBA", (W, H))
    d = draw_bg(img, accent_rgb, bg_mode)
    fpath = cfgmod.font_path()
    bpath = cfgmod.black_font_path()
    boxes = []

    # 徽章 chip（左上）
    badge = cover_meta.get("badge") or cfg["cover"]["badge"]
    if minutes:
        badge = badge if "分钟" in badge else "%s · %d分钟" % (badge, minutes)
    fb = font(bpath, 34)
    badge_box = chip(d, (COL_L, 96), badge, fb, 32, 16,
                     accent_rgb + (255,), text_fill=(20, 24, 30))
    boxes.append(badge_box)

    # 主标题（自动缩字号 + 关键词高亮）
    main_title = (cover_meta.get("main_title") or content.get("title", "论文解读")).strip()
    size, lines, hits = fit_title(d, main_title, bpath, COL_R - COL_L, 430,
                                  cover_meta.get("highlight"))
    hit_map = {}
    for li, a, b in (hits or []):
        hit_map.setdefault(li, (a, b))
    fy = 210
    for li, ln in enumerate(lines):
        f = font(bpath, size)
        fh = font(bpath, int(size * 1.08)) if li in hit_map else f
        x = COL_L
        if li in hit_map:
            i0, i1 = hit_map[li]
            pre, mid, post = ln[:i0], ln[i0:i1], ln[i1:]
            if pre:
                d.text((x, fy), pre, font=f, fill=(255, 255, 255),
                       stroke_width=max(4, size // 22), stroke_fill=(8, 12, 20))
                x += text_w(d, pre, f)
            mw = text_w(d, mid, fh)
            rounded(d, (x - 6, fy + size * 0.12, x + mw + 6, fy + size * 1.16), 16,
                    fill=accent_rgb + (255,))
            d.text((x, fy), mid, font=fh, fill=(18, 22, 28))
            x += mw
            if post:
                d.text((x, fy), post, font=f, fill=(255, 255, 255),
                       stroke_width=max(4, size // 22), stroke_fill=(8, 12, 20))
            line_box = (COL_L - 4, fy - 4, COL_R + 4, fy + int(size * 1.06))
        else:
            d.text((x, fy), ln, font=f, fill=(255, 255, 255),
                   stroke_width=max(4, size // 22), stroke_fill=(8, 12, 20))
            line_box = (COL_L - 4, fy - 4, COL_L + text_w(d, ln, f) + stroke_extra(size) + 4,
                        fy + int(size * 1.06))
        boxes.append(line_box)
        fy += int(size * 1.32)

    # 副标题：论文原题（浅灰小字）
    sub = (cover_meta.get("sub_title") or "").strip()
    sub_box = None
    if sub:
        fs = font(fpath, 30)
        slines = wrap_chars(d, sub, fs, COL_R - COL_L)[:2]
        if len(wrap_chars(d, sub, fs, COL_R - COL_L)) > 2:
            slines[-1] = slines[-1].rstrip() + "…"
        sy = min(fy + 18, 700)
        for k, sl in enumerate(slines):
            d.text((COL_L, sy + k * 40), sl, font=fs,
                   fill=(210, 220, 235) if bg_mode == "deep" else (70, 84, 100))
        sub_box = (COL_L - 4, sy - 6,
                   COL_L + max(text_w(d, sl, fs) for sl in slines) + 8,
                   sy + 40 * len(slines) + 4)
        boxes.append(sub_box)

    # 标签 chips
    ty = (sub_box[3] + 26) if sub_box else min(fy + 30, 800)
    ft = font(fpath, 30)
    tx = COL_L
    for tag in cover_meta.get("tags", [])[:4]:
        b = chip(d, (tx, ty), "# " + tag, ft, 18, 10,
                 (255, 255, 255, 26) if bg_mode == "deep" else (20, 40, 70, 22),
                 outline=(255, 255, 255, 90) if bg_mode == "deep" else (40, 70, 110, 110))
        boxes.append(b)
        tx = b[2] + 18
        if tx > COL_R - 160:
            break

    # 底部出处行
    src = (cover_meta.get("source") or "").strip()
    if src:
        fsx = font(fpath, 28)
        shown = src if text_w(d, src, fsx) < COL_R - COL_L else (
            wrap_chars(d, src, fsx, COL_R - COL_L)[0] + "…")
        d.text((COL_L, H - 96), shown, font=fsx, fill=(196, 210, 232) if bg_mode == "deep" else (90, 105, 125))
        boxes.append((COL_L - 4, H - 100, COL_L + text_w(d, shown, fsx) + 8, H - 58))

    # 右栏论文截图卡
    pic = Image.open(card_src)
    card_box, d = draw_card(img, pic, accent_rgb)
    boxes.append(card_box)

    # —— 图文重叠硬检测：所有文本块两两、文本块×卡片，均不得相交 ——
    bad = pairwise_overlap(boxes)
    if bad:
        raise SystemExit("[封面] 图文重叠：%s —— 改短 main_title/标签或去掉高亮后重画"
                         % "; ".join("%s∩%s" % (a, b) for a, b, _ in bad))

    img.convert("RGB").save(out_png)
    img.convert("RGB").resize((W * 2, H * 2), Image.LANCZOS).save(out_png_2x)
    return boxes


def stroke_extra(size):
    return max(4, size // 22) * 2


def pairwise_overlap(boxes):
    bad = []
    names = ["badge", "title1", "title2", "title3", "sub_title",
             "tag", "tag", "tag", "tag", "source", "card"]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if (a[0] - OVERLAP_MARGIN < b[2] and b[0] - OVERLAP_MARGIN < a[2]
                    and a[1] - OVERLAP_MARGIN < b[3] and b[1] - OVERLAP_MARGIN < a[3]):
                bad.append((names[min(i, len(names) - 1)], names[min(j, len(names) - 1)], (a, b)))
    return bad


def main():
    ap = argparse.ArgumentParser(description="生成 B 站风格视频封面（含重叠检测）")
    ap.add_argument("--content", required=True)
    ap.add_argument("--out", required=True, help="视频工作目录")
    ap.add_argument("--extract", help="pdf2ppt 工作目录（取 extract/cover.png 当卡图）")
    ap.add_argument("--slides-dir", help="无 cover.png 时用 slides/slide-01.png")
    ap.add_argument("--cover-json", help="封面文案（缺省自动兜底）")
    ap.add_argument("--minutes", type=int, default=0, help="预计视频分钟数（进徽章）")
    ap.add_argument("--config")
    args = ap.parse_args()

    cfg = cfgmod.load(args.config)
    content = json.load(open(args.content))
    work = os.path.abspath(args.out)
    cover_dir = os.path.join(work, "cover")
    os.makedirs(cover_dir, exist_ok=True)

    meta = {}
    cj = args.cover_json or os.path.join(work, "cover.json")
    if os.path.exists(cj):
        meta = json.load(open(cj))

    card_src = None
    for cand in (meta.get("card_image"),
                 os.path.join(args.extract or "", "extract", "cover.png"),
                 os.path.join(work, "..", "extract", "cover.png"),
                 os.path.join(args.slides_dir or "", "slide-01.png"),
                 os.path.join(work, "slides", "slide-01.png")):
        if cand and os.path.exists(cand):
            card_src = cand
            break
    if not card_src:
        raise SystemExit("没有可用卡图：给 --extract 指向 pdf2ppt 工作目录或 --slides-dir")

    title = content.get("title", "论文解读")
    safe = "".join(c if c.isalnum() or "一" <= c <= "鿿" else "_" for c in title)[:50].strip("_")
    out_png = os.path.join(work, "%s-cover.png" % safe)

    meta.setdefault("main_title", title)
    meta.setdefault("sub_title", "")
    # 自动兜底标签：取 content.json 封面副标题里的关键词不现实，给出通用兜底
    meta.setdefault("tags", [])
    boxes = render(content, meta, cfg, args.minutes, out_png,
                   os.path.join(cover_dir, "cover_2x.png"), card_src)
    meta["out"] = out_png
    meta["card_image_used"] = card_src
    meta["boxes"] = [[int(v) for v in b] for b in boxes]
    json.dump(meta, open(os.path.join(cover_dir, "cover_meta.json"), "w"),
              ensure_ascii=False, indent=1)
    print("封面：%s（%d 个文本块，重叠检测通过）" % (out_png, len(boxes) - 1))


if __name__ == "__main__":
    main()
