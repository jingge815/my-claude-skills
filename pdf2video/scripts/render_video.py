#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""渲染视频：timeline.json → 每页段视频 → 合成（人声+背景音乐+烧字幕）→ mp4。

画面层（逐帧 Python 渲染，喂给 ffmpeg 编码）：
  - 讲解光标：段切换时平滑滑到新讲解区域，到位有点击脉冲，页尾淡出；
  - 高亮与聚焦：当前讲解区域外轻微压暗、区域上描一圈高亮框，讲哪指哪；
  - 局部放大：长讲图段从 4K 底图裁图区推近（1080p 成片依然清晰），讲完拉回；
  - 开场封面卡缓慢推近（Ken Burns），页间黑场过渡。
音频层（ffmpeg 一条 filter_complex）：
  - 人声轨：逐页旁白按页起始偏移拼接；
  - 背景音乐：模板配方生成或自备文件，循环铺满全片，
    sidechaincompress 跟着人声闪避（有人声压低、无人声抬起）；
  - 限幅防削波，字幕经 libass 烧进画面（字体/字号/边距走模板配置）。

用法：
    python3 render_video.py --work <视频工作目录> --content content.json --title "论文题目"
    python3 render_video.py --work . --render-only     # 只出段视频（调动画用）
    python3 render_video.py --work . --assemble-only   # 只做合成（调音量/字幕用）
"""

import argparse
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cfgmod  # noqa: E402

VW, VH = 1920, 1080
ZOOM_EASE = 0.55
CURSOR_SIZE = 56


# ---------------------------------------------------------------- 素材预生成
def make_cursor():
    """经典指针箭头（白身黑边+投影），程序生成不依赖素材文件。"""
    s = CURSOR_SIZE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(10, 4), (10, 34), (17, 28), (22, 40), (27, 38), (22, 26), (31, 26)]
    sh = Image.new("RGBA", (s + 8, s + 8), (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon([(x + 3, y + 4) for x, y in pts], fill=(0, 0, 0, 110))
    sh = sh.filter(ImageFilter.GaussianBlur(2.2))
    img.alpha_composite(sh, (-2, -2))
    d.polygon(pts, fill=(255, 255, 255, 255), outline=(20, 24, 30, 255))
    d.line(pts + [pts[0]], fill=(20, 24, 30, 255), width=2)
    return np.asarray(img).astype(np.float32)


def rounded_overlay(box, color, stroke, fill_alpha, expand=0):
    """高亮层：圆角描边 + 软填充，整帧 RGBA。"""
    x0, y0, x1, y1 = box
    img = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if fill_alpha:
        d.rounded_rectangle((x0 + stroke // 2, y0 + stroke // 2, x1, y1), radius=18,
                            fill=color + (fill_alpha,))
        img = img.filter(ImageFilter.GaussianBlur(6))
        d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0, y0, x1, y1), radius=18, outline=color + (235,), width=stroke)
    return np.asarray(img).astype(np.float32)


def dim_mask(box):
    """聚焦压暗层：区域外 ×0.84，软边过渡。返回 (1080,1080) 灰度 0..1。"""
    x0, y0, x1, y1 = box
    img = Image.new("L", (VW, VH), 0)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((max(-40, x0 - 14), max(-40, y0 - 14),
                         min(VW + 40, x1 + 14), min(VH + 40, y1 + 14)), radius=24, fill=255)
    img = img.filter(ImageFilter.GaussianBlur(30))
    m = np.asarray(img).astype(np.float32) / 255.0
    return 0.84 + 0.16 * m


def ease(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def lerp_box(a, b, u):
    return [a[i] + (b[i] - a[i]) * u for i in range(4)]


def clamp_box(x, y, w, h):
    x = min(max(0.0, x), VW - w)
    y = min(max(0.0, y), VH - h)
    return x, y, w, h


# ---------------------------------------------------------------- 页面上下文
class PageCtx:
    def __init__(self, pg, work, fps, fade):
        self.pg = pg
        self.fps = fps
        self.fade = fade
        self.seg_dur = float(pg.get("lead_in", 0.8)) + float(pg.get("audio_dur") or 0) \
            + float(pg.get("lead_out", 1.3))
        self.lead_in = float(pg.get("lead_in", 0.8))
        self.slide_path = os.path.join(work, pg["slide"])
        self.base4k = Image.open(self.slide_path).convert("RGB")
        if self.base4k.size != (VW, VH):
            self.base1080 = np.asarray(
                self.base4k.resize((VW, VH), Image.LANCZOS), dtype=np.uint8)
        else:
            self.base1080 = np.asarray(self.base4k, dtype=np.uint8)
        self.kb = pg.get("ken_burns")
        self.zooms = pg.get("zooms") or []
        self.cursor = sorted(pg.get("cursor") or [], key=lambda k: k["t"])
        self.segments = pg.get("segments") or []
        self.regs = pg.get("regions") or {}
        self.layers = {}       # region -> (dim, highlight_rgba)
        self.cursor_img = make_cursor()

    def region_box(self, region, fig_i):
        b = None
        if region == "figure" and fig_i is not None:
            figs = self.regs.get("figures") or []
            if fig_i < len(figs) and figs[fig_i]:
                b = figs[fig_i]
        elif region == "summary" and self.regs.get("summary"):
            b = self.regs["summary"]
        elif region == "title" and self.regs.get("title"):
            b = self.regs["title"]
        elif self.regs.get("body"):
            b = self.regs["body"]
        if not b:
            return None
        return (max(6, int(b[0] - 12)), max(6, int(b[1] - 12)),
                min(VW - 6, int(b[0] + b[2] + 12)), min(VH - 6, int(b[1] + b[3] + 12)))

    def layer(self, key, region, fig_i):
        cache_key = "%s_%s_%s" % (key, region, fig_i)
        if cache_key not in self.layers:
            box = self.region_box(region, fig_i)
            if box is None:
                self.layers[cache_key] = None
            elif key == "dim":
                self.layers[cache_key] = dim_mask(box)
            else:
                self.layers[cache_key] = rounded_overlay(
                    box, (255, 213, 74), 5, fill_alpha=30)
        return self.layers[cache_key]

    def zoom_rect(self, t):
        for z in self.zooms:
            zt0, zt1 = z["t"]
            if zt0 <= t <= zt1:
                box = z["box"]
                if t < zt0 + ZOOM_EASE:
                    u = ease((t - zt0) / ZOOM_EASE)
                elif t > zt1 - ZOOM_EASE:
                    u = ease((zt1 - t) / ZOOM_EASE)
                else:
                    u = 1.0
                return lerp_box([0, 0, VW, VH], box, u), u
        if self.kb:  # 开场封面：整段缓慢推近
            u = min(1.0, t / max(0.1, self.seg_dur))
            z = 0.035
            return lerp_box([0, 0, VW, VH],
                            [VW * z * 0.4, VH * z * 0.4, VW * (1 - z), VH * (1 - z)], u), u * 0.5
        return None, 0.0

    def crop(self, rect):
        if abs(rect[0]) < 0.5 and abs(rect[1]) < 0.5 \
                and abs(rect[2] - VW) < 0.5 and abs(rect[3] - VH) < 0.5:
            return self.base1080
        x, y, w, h = clamp_box(rect[0], rect[1], rect[2], rect[3])
        sx, sy = self.base4k.width / VW, self.base4k.height / VH
        c = self.base4k.crop((int(x * sx), int(y * sy),
                              int((x + w) * sx), int((y + h) * sy)))
        return np.asarray(c.resize((VW, VH), Image.LANCZOS), dtype=np.uint8)

    def cursor_at(self, t):
        """(x, y, alpha 0..1)。"""
        ks = self.cursor
        if len(ks) < 2 or t < ks[0]["t"]:
            return None, None, 0.0
        alpha = 1.0
        for i in range(len(ks) - 1):
            k0, k1 = ks[i], ks[i + 1]
            if k0["t"] <= t <= k1["t"]:
                u = ease((t - k0["t"]) / max(1e-6, k1["t"] - k0["t"]))
                return (k0["x"] + (k1["x"] - k0["x"]) * u,
                        k0["y"] + (k1["y"] - k0["y"]) * u,
                        alpha)
        # 兜底返回末帧
        last = ks[-1]
        if last.get("fade_out"):
            alpha = max(0.0, 1.0 - (t - last["t"]) / 0.35)
            return last["x"], last["y"], alpha
        return last["x"], last["y"], alpha

    def click_pulse(self, t):
        for k in self.cursor:
            if k.get("click") and 0 <= t - k["t"] <= 0.38:
                u = (t - k["t"]) / 0.38
                return k["x"], k["y"], u
        return None

    def active_region(self, t):
        for seg in self.segments:
            if seg["t"][0] - 0.15 <= t <= seg["t"][1] + 0.45:
                return seg["region"], seg.get("fig")
        return None, None

    def frame(self, t):
        zoom_rect, zoom_amt = self.zoom_rect(t)
        if zoom_rect is not None:
            frame = self.crop(zoom_rect).astype(np.float32)
        else:
            frame = self.base1080.astype(np.float32)

        region, fig_i = (None, None) if zoom_amt > 0.5 else self.active_region(t)
        if region:
            dm = self.layer("dim", region, fig_i)
            if dm is not None:
                frame *= dm[:, :, None]
            hl = self.layer("hl", region, fig_i)
            if hl is not None:
                a = (0.62 + 0.30 * np.sin(t * 2.6)) * (1.0 - zoom_amt)
                if a > 0.02:
                    ha = hl[:, :, 3:4] / 255.0 * a
                    frame = frame * (1 - ha) + hl[:, :, :3] * ha

        x, y, ca = self.cursor_at(t)
        ca *= (1.0 - zoom_amt)
        if x is not None and ca > 0.02 and zoom_amt < 0.5:
            cp = self.click_pulse(t)
            if cp is not None:
                cx, cy, u = cp
                r = 10 + 58 * ease(u)
                ring = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
                ImageDraw.Draw(ring).ellipse(
                    (cx - r, cy - r, cx + r, cy + r),
                    outline=(255, 255, 255, int(170 * (1 - u))), width=3)
                ra = np.asarray(ring, dtype=np.float32)
                a = ra[:, :, 3:4] / 255.0
                frame = frame * (1 - a) + ra[:, :, :3] * a
            ci = self.cursor_img
            xi, yi = int(x) - 6, int(y) - 4
            if 0 <= xi < VW - ci.shape[1] and 0 <= yi < VH - ci.shape[0]:
                a = ci[:, :, 3:4] / 255.0 * ca
                frame[yi:yi + ci.shape[0], xi:xi + ci.shape[1]] = \
                    frame[yi:yi + ci.shape[0], xi:xi + ci.shape[1]] * (1 - a) + ci[:, :, :3] * a

        # 页首尾黑场淡入淡出
        f = self.fade
        if f > 0 and (t < f or t > self.seg_dur - f):
            ramp = min(t, self.seg_dur - t) / f
            frame *= max(0.0, ramp)
        return frame.clip(0, 255).astype(np.uint8)


# ---------------------------------------------------------------- 渲染与合成
def render_segments(timeline, work, cfg, only=None):
    fps = timeline["fps"]
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    seg_dir = os.path.join(work, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    for pg in timeline["pages"]:
        idx = pg["idx"]
        if only and idx not in only:
            continue
        ctx = PageCtx(pg, work, fps, float(timeline.get("fade", 0.5)))
        n = int(round(ctx.seg_dur * fps))
        out = os.path.join(seg_dir, "seg-%02d.mp4" % idx)
        cmd = [ff, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", "%dx%d" % (VW, VH), "-r", str(fps), "-i", "-",
               "-an", "-c:v", "libx264", "-preset", cfg["video"].get("preset", "veryfast"),
               "-crf", str(cfg["video"].get("crf", 18)), "-pix_fmt", "yuv420p", out]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for k in range(n):
            proc.stdin.write(ctx.frame(k / fps).tobytes())
        proc.stdin.close()
        proc.wait()
        print("  段视频第%d页：%.1fs %d 帧 → %s"
              % (idx, ctx.seg_dur, n, os.path.basename(out)))
        del ctx


def _sorted_lines(timeline):
    ls = [l for pg in timeline["pages"] for l in (pg.get("lines") or [])]
    return sorted(ls, key=lambda x: x["t"][0])


def _ass_ts(sec):
    sec = max(0.0, float(sec))
    cs = int(round(sec * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return "%d:%02d:%02d.%02d" % (h, m, s, cs)


def assemble(timeline, work, content, cfg, title):
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    fps = timeline["fps"]
    total = timeline["total_dur"]
    seg_dir = os.path.join(work, "segments")

    # 1) 拼接段视频（参数一致，流拷贝）
    concat = os.path.join(work, "concat.txt")
    with open(concat, "w") as f:
        for pg in timeline["pages"]:
            f.write("file '%s'\n" % os.path.join(seg_dir, "seg-%02d.mp4" % pg["idx"]))
    noaudio = os.path.join(work, "_video_noaudio.mp4")
    subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", concat,
                    "-c", "copy", noaudio], check=True, capture_output=True)

    # 2) 人声轨：逐页按页起始偏移，amix 相加
    # 注意输入编号：noaudio 占 ffmpeg 输入 0，旁白从 1 起，滤镜标签要跟着偏移
    inputs, filters = [], []
    narr_labels = []
    fi = 0   # ffmpeg 输入索引（0 已被 noaudio 占用）
    for pg in timeline["pages"]:
        if not pg.get("audio"):
            continue
        apath = os.path.join(work, pg["audio"])
        if not os.path.exists(apath):
            continue
        inputs += ["-i", apath]
        fi += 1
        delay_ms = int(round((pg["t0"] + pg["lead_in"]) * 1000))
        filters.append("[%d]adelay=%d:all=1[v%d]" % (fi, delay_ms, pg["idx"]))
        narr_labels.append("[v%d]" % pg["idx"])
    if not narr_labels:
        raise SystemExit("没有任何旁白音频，先跑 tts_narration.py")
    filters.append("%samix=inputs=%d:normalize=0[narr]" % ("".join(narr_labels), len(narr_labels)))

    # 3) 背景音乐：循环铺满，跟人声闪避（人声轨统一到 44.1k 立体声）
    bgm_path = cfg["bgm"].get("path") or os.path.join(work, "bgm", "bgm.wav")
    if not os.path.exists(bgm_path):
        raise SystemExit("缺背景音乐：%s（先跑 make_bgm.py，或在 config [bgm].path 指定）" % bgm_path)
    inputs += ["-stream_loop", "-1", "-i", bgm_path]
    bi = fi + 1
    gain_db = float(cfg["bgm"].get("gain_db", -26))
    duck_db = float(cfg["bgm"].get("duck_db", -8))
    total_f = total
    filters.append(
        "[narr]aresample=44100,aformat=channel_layouts=stereo,asplit=2[na][nb];"
        "[na]volume=%.1fdB[narrg];"
        "[%d]atrim=0:%.2f,volume=%.1fdB,afade=t=in:st=0:d=0.9[bgm0];"
        "[bgm0][nb]sidechaincompress=threshold=0.02:ratio=6:attack=25:release=250"
        ":makeup=1[bgmd];"
        "[bgmd]volume=%.1fdB,afade=t=out:st=%.2f:d=1.5[bgm1];"
        "[narrg][bgm1]amix=inputs=2:normalize=0,alimiter=limit=%.2f[aout]"
        % (float(cfg["mix"].get("narration_gain_db", 0)), bi, total_f, gain_db,
           duck_db, max(0.0, total_f - 1.5),
           float(cfg["mix"].get("limiter", 0.95))))
    fc = ";".join(filters)

    safe = "".join(c if c.isalnum() or "一" <= c <= "鿿" else "_" for c in title)[:50].strip("_")
    out_mp4 = os.path.join(work, "%s-video.mp4" % safe)

    # 生成 ASS 字幕（libass 对 ASS 样式全量支持；force_style 会丢 BorderStyle）：
    # 白字 + 深描边 + 柔影，浅色总结条上也读得清；字号/边距从模板配置换算到 1080p
    style = timeline.get("subtitle_style") or {}
    sz = float(style.get("size", 15))
    ass_size = int(round(sz * 1080 / 288))
    ass_margin = int(round(float(style.get("margin_v", 30)) * 1.5))
    ass_outline = round(sz * 0.24, 1)
    ass_path = os.path.join(work, "subtitles.ass")
    with open(ass_path, "w") as f:
        f.write("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n"
                "WrapStyle: 0\n\n[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
                "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
                "MarginL, MarginR, MarginV, Encoding\n"
                "Style: Sub,%s,%d,&H00FFFFFF,&H00FFFFFF,&H00151A22,&H00151A22,0,0,0,0,"
                "100,100,0,0,1,%s,1.5,2,60,60,%d,1\n\n[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
                "Effect, Text\n"
                % (style.get("font", "Noto Sans CJK SC"), ass_size, ass_outline, ass_margin))
        trail = "，。！？；：、,.!?;:"
        for l in _sorted_lines(timeline):
            f.write("Dialogue: 0,%s,%s,Sub,,0,0,0,,%s\n"
                    % (_ass_ts(l["t"][0]), _ass_ts(l["t"][1]),
                       l["text"].replace("\n", " ").rstrip(trail)))
    srt = ass_path
    cmd = [ff, "-y", "-i", noaudio, *inputs,
           "-filter_complex", fc,
           "-map", "0:v:0", "-map", "[aout]",
           "-vf", "subtitles=%s" % srt.replace("\\", "\\\\").replace(":", "\\:"),
           "-c:v", "libx264", "-preset", cfg["video"].get("preset", "veryfast"),
           "-crf", str(cfg["video"].get("crf", 18)), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-shortest", out_mp4]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-3000:], file=sys.stderr)
        raise SystemExit("合成失败（ffmpeg 返回 %d）" % r.returncode)

    # 字幕软文件也作为交付物
    safe_srt = os.path.join(work, "%s-字幕.srt" % safe)
    if os.path.abspath(srt) != os.path.abspath(safe_srt):
        subprocess.run(["cp", srt, safe_srt], check=False)

    report = {"video": out_mp4, "total_dur": total,
              "pages": [{"idx": p["idx"], "kind": p["kind"], "t0": p["t0"],
                         "seg_dur": p.get("seg_dur"),
                         "audio": p.get("audio"),
                         "audio_dur": p.get("audio_dur"),
                         "n_lines": len(p.get("lines") or []),
                         "n_segments": len(p.get("segments") or []),
                         "n_zooms": len(p.get("zooms") or [])} for p in timeline["pages"]]}
    json.dump(report, open(os.path.join(work, "render_report.json"), "w"),
              ensure_ascii=False, indent=1)
    print("成片：%s（%.1fs）" % (out_mp4, total))
    print("软字幕：%s" % safe_srt)
    return out_mp4


def main():
    ap = argparse.ArgumentParser(description="渲染讲解视频")
    ap.add_argument("--work", required=True)
    ap.add_argument("--content", required=True)
    ap.add_argument("--title", help="成片文件名用的论文题目（缺省取 content.json title）")
    ap.add_argument("--config")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--assemble-only", action="store_true")
    ap.add_argument("--only", help="只重渲染这几页（页序 idx），如 0,3")
    args = ap.parse_args()

    cfg = cfgmod.load(args.config)
    work = os.path.abspath(args.work)
    timeline = json.load(open(os.path.join(work, "timeline.json")))
    content = json.load(open(args.content))
    title = args.title or content.get("title", "论文解读")

    if not args.assemble_only:
        only = None
        if args.only:
            only = {int(x) for x in args.only.replace(",", " ").split() if x.strip().isdigit()}
        render_segments(timeline, work, cfg, only)
    if not args.render_only:
        assemble(timeline, work, content, cfg, title)


if __name__ == "__main__":
    main()
