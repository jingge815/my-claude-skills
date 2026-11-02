"""用 layout 引擎的同一份元素，把每页幻灯片渲染成 1920x1080 PNG（视频帧 + 预览核对用）。

用法: pr.sh render_frames <content.json> [--out 目录] [--sheet]
  --sheet  额外生成 contact_sheet.png（所有页缩略图拼图），用于快速目检
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

from common import (RENDER_DPI, SLIDE_H, SLIDE_W, THEME, die, hex_rgb, load_content, load_figures,
                    log, pil_font, plain)
from layout import BULLET_CHAR_SCALE, SlideError, layout_slide, overflow_report

PX = RENDER_DPI  # 每英寸像素


def px(v):
    return int(round(v * PX))


def pt_px(size_pt):
    return max(1, int(round(size_pt / 72.0 * PX)))


def draw_text(img, d, e):
    total = e["content_h"]
    if e["valign"] == "m":
        y = e["y"] + (e["h"] - total) / 2
    elif e["valign"] == "b":
        y = e["y"] + e["h"] - total
    else:
        y = e["y"]
    for fp in e["fitted"]:
        y += fp["space_before_in"]
        size = fp["size"]
        for li, line in enumerate(fp["lines"]):
            base_y = y + fp["line_h"] * 0.5 + size / 72.0 * 0.36  # 行框内大致居中的基线
            x0 = e["x"] + fp["indent"]
            # 对齐
            widths = [pil_font(pt_px(size), fp["bold"] or b).getlength(t) for t, b in line]
            lw = sum(widths) / PX
            if fp["align"] == "c":
                x0 = e["x"] + (e["w"] - lw) / 2
            elif fp["align"] == "r":
                x0 = e["x"] + e["w"] - lw
            if li == 0 and fp["bullet"]:
                bs = size * BULLET_CHAR_SCALE[fp["level"]]
                bcolor = THEME["accent"] if fp["level"] == 0 else THEME["muted"]
                if fp["color"] == THEME["white"]:
                    bcolor = THEME["highlight"]
                bf = pil_font(pt_px(bs), False)
                d.text((px(e["x"] + fp["indent"] - 0.28), px(base_y - (size - bs) / 72.0 * 0.45)),
                       fp["bullet"], font=bf, fill=hex_rgb(bcolor), anchor="ls")
            cx = x0
            for (t, emph), w in zip(line, widths):
                bold = fp["bold"] or emph
                color = THEME["accent"] if emph and fp["color"] == THEME["text"] else fp["color"]
                d.text((px(cx), px(base_y)), t, font=pil_font(pt_px(size), bold),
                       fill=hex_rgb(color), anchor="ls")
                cx += w / PX
            y += fp["line_h"]


def draw_table(d, e):
    y = e["y"]
    for ri, (cells, h) in enumerate(zip(e["cells"], e["row_h"])):
        x = e["x"]
        fill = THEME["primary"] if ri == 0 else (THEME["soft"] if ri % 2 == 0 else THEME["white"])
        d.rectangle([px(x), px(y), px(x + e["w"]), px(y + h)], fill=hex_rgb(fill))
        for ci, lines in enumerate(cells):
            w = e["col_w"][ci]
            size = e["size"]
            lh = size * 1.2 / 72.0
            th = lh * len(lines)
            ty = y + (h - th) / 2
            color = THEME["white"] if ri == 0 else THEME["text"]
            for line in lines:
                widths = [pil_font(pt_px(size), ri == 0 or b).getlength(t) for t, b in line]
                lw = sum(widths) / PX
                cx = x + (w - lw) / 2 if (ri == 0 or ci > 0) else x + e["pad_x"]
                for (t, b), tw in zip(line, widths):
                    c = THEME["accent"] if b and ri > 0 else color
                    d.text((px(cx), px(ty + lh * 0.5 + size / 72.0 * 0.36)), t,
                           font=pil_font(pt_px(size), ri == 0 or b), fill=hex_rgb(c), anchor="ls")
                    cx += tw / PX
                ty += lh
            x += w
        y += h
    # 网格线
    line = hex_rgb(THEME["line"])
    yy = e["y"]
    for h in e["row_h"][:-1]:
        yy += h
        d.line([px(e["x"]), px(yy), px(e["x"] + e["w"]), px(yy)], fill=line, width=1)
    d.rectangle([px(e["x"]), px(e["y"]), px(e["x"] + e["w"]), px(e["y"] + e["h"])], outline=line, width=1)


def render_slide(els) -> Image.Image:
    img = Image.new("RGB", (px(SLIDE_W), px(SLIDE_H)), "white")
    d = ImageDraw.Draw(img)
    for e in els:
        k = e["kind"]
        if k == "rect":
            box = [px(e["x"]), px(e["y"]), px(e["x"] + e["w"]) - 1, px(e["y"] + e["h"]) - 1]
            d.rectangle(box, fill=hex_rgb(e["fill"]) if e["fill"] else None,
                        outline=hex_rgb(e["line"]) if e["line"] else None,
                        width=max(1, int(e.get("line_w", 1) * PX / 72)) if e["line"] else 0)
        elif k == "image":
            with Image.open(e["file"]) as src:
                im = src.convert("RGB").resize((px(e["w"]), px(e["h"])), Image.LANCZOS)
            img.paste(im, (px(e["x"]), px(e["y"])))
        elif k == "text":
            draw_text(img, d, e)
        elif k == "table":
            draw_table(d, e)
    return img


def render_all(content_path: Path, out_dir: Path | None = None, sheet: bool = False) -> list[Path]:
    content = load_content(content_path)
    meta = content.get("meta", {})
    figs = load_figures(content_path, content)
    slides = content.get("slides") or die("content.json 里没有 slides")
    out_dir = out_dir or (Path(content_path).resolve().parent / "build" / "frames")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("slide*.png"):
        old.unlink()
    paths = []
    for i, s in enumerate(slides, 1):
        try:
            els = layout_slide(s, meta, figs, content_path, i, len(slides))
        except SlideError as ex:
            die(f"第 {i} 页（{s.get('title', s.get('type'))}）：{ex}")
        for m in overflow_report(els):
            log(f"警告：第 {i} 页：{m}")
        p = out_dir / f"slide{i:02d}.png"
        render_slide(els).save(p)
        paths.append(p)
    if sheet:
        make_sheet(paths, out_dir.parent / "contact_sheet.png")
    log(f"已渲染 {len(paths)} 帧到 {out_dir}")
    return paths


def make_sheet(paths, out, cols=3, tw=640):
    th = int(tw * SLIDE_H / SLIDE_W)
    rows = (len(paths) + cols - 1) // cols
    gap = 16
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * gap, rows * (th + 30) + (rows + 1) * gap), (225, 228, 232))
    d = ImageDraw.Draw(sheet)
    for i, p in enumerate(paths):
        r, c = divmod(i, cols)
        x = gap + c * (tw + gap)
        y = gap + r * (th + 30 + gap)
        with Image.open(p) as im:
            sheet.paste(im.resize((tw, th), Image.LANCZOS), (x, y + 30))
        d.text((x, y + 4), f"#{i + 1}", font=pil_font(20, True), fill=(40, 40, 40))
    sheet.save(out)
    log(f"缩略图总览：{out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("content")
    ap.add_argument("--out")
    ap.add_argument("--sheet", action="store_true")
    a = ap.parse_args()
    render_all(Path(a.content), Path(a.out) if a.out else None, a.sheet)


if __name__ == "__main__":
    main()
