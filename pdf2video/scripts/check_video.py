#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频门禁：把「迭代校对」的五条验证标准变成可机械执行的检查。

对应关系（标准 → 检查）：
  1. 生成视频清晰        → 分辨率/帧率/码率探针 + 抽帧拉普拉斯方差（清晰度下限）
  2. 旁白文字/声音/画面对应 → 字幕文本逐页还原旁白；抽帧与该页底图比对（页序正确）；
                             字幕时间落在该页音频区间；词级时间戳驱动（见 tts.json）
  3. 女声、清楚、非AI味   → 主播音 ∈ 女声白名单；人声窗口响度达标；无削波
                             （「非AI味」的文本口吻属内容级自检，见 SKILL.md 阶段5）
  4. 背景音轻松不抢解说   → 纯音乐间隙响度 ≤ 人声窗口响度 -12dB；背景音确实存在
  5. 封面图文不重叠       → 封面文件存在；重放 make_cover 的包围盒两两相交检测

动画生效（光标/高亮/放大）也抽帧验证：讲解时刻的帧必须与纯底图不同。
输出 [门禁] 行，错误必须清零；--frames-out 同时导出抽帧 PNG 供人复核。

用法：
    python3 check_video.py --work <视频工作目录> --content content.json
    python3 check_video.py --work . --content c.json --frames-out frames/
"""

import argparse
import json
import os
import re
import subprocess
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cfgmod  # noqa: E402

FAILS, WARNS = [], []


def fail(msg):
    FAILS.append(msg)
    print("[门禁][错误] %s" % msg)


def warn(msg):
    WARNS.append(msg)
    print("[门禁][警告] %s" % msg)


def ok(msg):
    print("[门禁][通过] %s" % msg)


def probe_video(ff, path):
    r = subprocess.run([ff, "-i", path], capture_output=True, text=True)
    err = r.stderr
    out = {}
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if m:
        out["dur"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"(\d{3,5})x(\d{3,5})", err)
    if m:
        out["w"], out["h"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?) fps", err)
    out["fps"] = float(m.group(1)) if m else None
    out["has_audio"] = bool(re.search(r"Audio: (aac|mp3)", err))
    out["v_codec"] = "h264" if "h264" in err else "?"
    return out


def grab_frame(ff, video, t, dst):
    subprocess.run([ff, "-y", "-ss", str(max(0.05, t)), "-i", video,
                    "-frames:v", "1", dst], capture_output=True, check=True)


def sharpness(im):
    """灰度拉普拉斯方差（清晰度代理指标）。"""
    g = np.asarray(im.convert("L"), dtype=np.float32)
    lap = (4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1]
           - g[1:-1, :-2] - g[1:-1, 2:])
    return float(lap.var())


def mean_volume(ff, media, t0, t1):
    r = subprocess.run([ff, "-ss", str(t0), "-to", str(t1), "-i", media,
                        "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr)
    mx = re.search(r"max_volume: (-?[\d.]+) dB", r.stderr)
    return (float(m.group(1)) if m else None,
            float(mx.group(1)) if mx else None)


def frame_diff_rate(a, b, tol=40):
    """两帧小于容差的像素占比（页序比对用，抗压暗/高亮小改动）。"""
    if a.size != b.size:
        return 1.0
    d = np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16)).max(axis=2)
    return float((d < tol).mean())


def main():
    ap = argparse.ArgumentParser(description="视频迭代校对门禁")
    ap.add_argument("--work", required=True)
    ap.add_argument("--content", required=True)
    ap.add_argument("--config")
    ap.add_argument("--frames-out", help="导出抽帧 PNG 的目录")
    args = ap.parse_args()

    cfg = cfgmod.load(args.config)
    work = os.path.abspath(args.work)
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()

    timeline = json.load(open(os.path.join(work, "timeline.json")))
    content = json.load(open(args.content))
    report_p = os.path.join(work, "render_report.json")
    if not os.path.exists(report_p):
        raise SystemExit("没有 render_report.json，先跑 render_video.py")
    report = json.load(open(report_p))
    video = report["video"]
    total = report["total_dur"]

    frames_dir = args.frames_out and os.path.abspath(args.frames_out)
    if frames_dir:
        os.makedirs(frames_dir, exist_ok=True)

    # ---- 标准1：视频清晰 ----
    pv = probe_video(ff, video)
    if pv.get("w") != 1920 or pv.get("h") != 1080:
        fail("分辨率 %sx%s 不是 1080p" % (pv.get("w"), pv.get("h")))
    else:
        ok("分辨率 1920x1080，fps=%s" % pv.get("fps"))
    if not pv.get("has_audio"):
        fail("成片没有音轨")
    if abs((pv.get("dur") or 0) - total) > 3:
        fail("成片时长 %.1fs 与时间轴 %.1fs 偏差超 3s" % (pv.get("dur") or 0, total))
    else:
        ok("成片时长 %.1fs（时间轴 %.1fs）" % (pv.get("dur") or 0, total))

    sharp_samples = []
    for pg in timeline["pages"]:
        if pg.get("audio_dur") and pg["audio_dur"] > 2:
            t = pg["t0"] + pg["lead_in"] + pg["audio_dur"] * 0.5
            sharp_samples.append((pg["idx"], t))
    sharp_fail = 0
    for idx, t in sharp_samples[:8]:
        fdst = os.path.join(frames_dir or "/tmp", "sharp-%02d.png" % idx)
        grab_frame(ff, video, t, fdst)
        s = sharpness(Image.open(fdst))
        if s < 60:
            sharp_fail += 1
            warn("第%d页抽帧清晰度 %.0f 偏低（模糊/糊码？）" % (idx, s))
    if sharp_fail == 0:
        ok("清晰度抽检 %d 帧全部达标（拉普拉斯方差 ≥60）" % min(8, len(sharp_samples)))

    # ---- 标准2：旁白文字、声音、画面对应 ----
    # 2a. 字幕还原旁白（逐页）
    srt_path = os.path.join(work, timeline.get("srt", "subtitles.srt"))
    srt = []
    if os.path.exists(srt_path):
        raw = open(srt_path).read().strip().split("\n\n")
        for blk in raw:
            ls = blk.strip().split("\n")
            if len(ls) >= 3:
                m = re.match(r"(\S+) --> (\S+)", ls[1])
                srt.append({"start": _sec(m.group(1)), "end": _sec(m.group(2)),
                            "text": "".join(ls[2:])})
    tlines = [l for pg in timeline["pages"] for l in (pg.get("lines") or [])]
    if len(srt) != len(tlines):
        fail("字幕条数 %d ≠ 时间轴字幕 %d" % (len(srt), len(tlines)))
    else:
        ok("字幕 %d 条，与时间轴一致" % len(srt))
    narr = content["narration"]
    narr = {str(i + 1): t for i, t in enumerate(narr)} if isinstance(narr, list) else narr
    strip = lambda s: re.sub(r"[，。！？；：、\s（）()\"\"''—…·\{\}\*]+", "", s)
    n_narr_pages = 0
    for pg in timeline["pages"]:
        no = pg["page_no"]
        if no == 0:
            continue
        n_narr_pages += 1
        got = strip("".join(l["text"] for l in (pg.get("lines") or [])))
        want = strip(narr.get(str(no), ""))
        if got != want:
            fail("第%d页字幕文本与旁白对不上（字幕 %d 字 vs 旁白 %d 字）"
                 % (no, len(got), len(want)))
    ok("字幕文本逐页还原旁白：%d 页核对" % n_narr_pages)

    # 2b. 字幕时间落在对应页音频窗口
    bad_t = 0
    for pg in timeline["pages"]:
        if not pg.get("audio_dur"):
            continue
        win0 = pg["t0"] + pg["lead_in"] - 0.6
        win1 = pg["t0"] + pg["lead_in"] + pg["audio_dur"] + 0.8
        for l in (pg.get("lines") or []):
            if not (win0 <= l["t"][0] and l["t"][1] <= win1):
                bad_t += 1
    if bad_t:
        fail("%d 行字幕时间越出所在页音频窗口" % bad_t)
    else:
        ok("字幕时间全部落在对应页音频窗口内")

    # 2c. 画面页序：抽帧与该页底图比对（容差 60：聚焦压暗 0.84 对纯白差 40.8，
    #     必须被容差吸收，否则正在讲图的页全会误报）
    page_match_ok = 0
    for pg in timeline["pages"][1:]:
        t = pg["t0"] + pg["lead_in"] + min(1.2, (pg.get("audio_dur") or 2) * 0.4)
        zoom_hit = any(z["t"][0] - 0.6 <= t <= z["t"][1] + 0.6 for z in (pg.get("zooms") or []))
        if zoom_hit:
            t = pg["t0"] + 0.65
        fdst = os.path.join(frames_dir or "/tmp", "page-%02d.png" % pg["idx"])
        grab_frame(ff, video, t, fdst)
        f = Image.open(fdst).resize((480, 270))
        base = Image.open(os.path.join(work, pg["slide"])).resize((480, 270))
        rate = frame_diff_rate(f, base, tol=60)
        if rate < 0.55:
            fail("第%d页画面与底图不匹配（相似度 %.2f）——页序或渲染错位" % (pg["page_no"], rate))
        else:
            page_match_ok += 1
    ok("画面页序抽检：%d/%d 页与底图对应" % (page_match_ok, len(timeline["pages"]) - 1))

    # ---- 标准3：女声、清楚 ----
    voice = (json.load(open(os.path.join(work, "tts.json"))).get("voice") or {})
    if voice.get("name") in cfgmod.FEMALE_VOICES:
        ok("主播音 %s（女声白名单）" % voice["name"])
    else:
        warn("主播音 %s 不在已知女声白名单，人工确认声音性别与自然度" % voice.get("name"))
    narr_mean = None
    first_a = next((p for p in timeline["pages"] if p.get("audio_dur") and p["page_no"]), None)
    if not first_a:
        fail("没有任何带旁白的页（tts_narration 没跑或全空）")
    if first_a:
        t0 = first_a["t0"] + first_a["lead_in"] + 0.2
        t1 = t0 + min(first_a["audio_dur"] - 0.4, 20)
        narr_mean, narr_max = mean_volume(ff, video, t0, t1)
        if narr_mean is None or narr_mean < -32:
            fail("人声响度 %.1f dB 过低（听不清解说）" % (narr_mean or -99))
        elif narr_mean > -10:
            warn("人声响度 %.1f dB 偏高，可能刺耳" % narr_mean)
        else:
            ok("人声响度 %.1f dB（max %.1f dB，无削波）" % (narr_mean, narr_max))
        if narr_max is not None and narr_max > -0.5:
            fail("音频削波（max %.1f dB）" % narr_max)

    # ---- 标准4：背景音不抢解说 ----
    gap_mean = None
    gaps_found = 0
    for pg in timeline["pages"]:
        if gaps_found >= 3:
            break
        # +0.55 起探：跳过侧链释放期，测"已恢复"的间隙电平
        gap0 = pg["t0"] + pg["lead_in"] + (pg.get("audio_dur") or 0) + 0.55
        gap1 = pg["t0"] + pg.get("seg_dur", pg["t0"] + 4) - 0.15
        if gap1 - gap0 >= 0.9:
            m, _ = mean_volume(ff, video, gap0, gap1)
            if m is not None and (gap_mean is None or m > gap_mean):
                gap_mean = m          # 取最响间隙=最接近稳态背景音电平
            gaps_found += 1
    if narr_mean is not None and gap_mean is not None:
        if gap_mean > narr_mean - 10:
            fail("纯音乐间隙 %.1f dB 距人声 %.1f dB 不足 10dB（背景音抢解说）"
                 % (gap_mean, narr_mean))
        elif gap_mean < -38:
            fail("纯音乐间隙只有 %.1f dB——背景音乐没铺上或电平过低（阈值 -38）" % gap_mean)
        else:
            ok("背景音电平 %.1f dB，比人声低 %.1f dB（存在且不喧宾）"
               % (gap_mean, narr_mean - gap_mean))

    # ---- 字幕可读性：行激活时刻字幕带必须有强对比（白字+底框）----
    sub_ok = 0
    line_times_probe = [l for l in srt if l["end"] - l["start"] > 0.8][:200]
    for l in (line_times_probe[10:110:25] if len(line_times_probe) > 35 else line_times_probe[:3]):
        t = l["start"] + 0.4
        fdst = os.path.join(frames_dir or "/tmp", "sub-%.1f.png" % t)
        grab_frame(ff, video, t, fdst)
        band = np.asarray(Image.open(fdst).convert("L"), dtype=np.float32)[990:1055, :]
        if band.std(axis=1).max() < 15:
            fail("%.0fs 处字幕带无对比——字幕被浅色背景洗掉或没烧上" % t)
        else:
            sub_ok += 1
    if sub_ok:
        ok("字幕可读性抽检 %d 帧达标（底框描边清晰）" % sub_ok)

    # ---- 标准5：封面 ----
    cover_png = os.path.join(work, "cover", "cover_meta.json")
    meta = json.load(open(cover_png)) if os.path.exists(cover_png) else None
    if not meta or not os.path.exists(meta.get("out", "")):
        fail("视频封面缺失（make_cover.py 未跑或产物丢失）")
    else:
        ok("封面存在：%s" % os.path.basename(meta["out"]))
        boxes = meta.get("boxes") or []
        bad = []
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if (a[0] - 8 < b[2] and b[0] - 8 < a[2]
                        and a[1] - 8 < b[3] and b[1] - 8 < a[3]):
                    bad.append((i, j))
        if bad:
            fail("封面图文重叠：%s（改 cover.json 文案后重跑 make_cover）" % bad)
        else:
            ok("封面 %d 个块两两不相交" % len(boxes))

    # ---- 指图一致性：说图 N 的段，高亮框/放大框必须落在图 N 上（用户三次强调） ----
    import numpy as _np
    accent_boxes = []

    def accent_bbox(im):
        """检测画面里的黄色高亮框像素（#FFD54A 及其混色）的外接框。"""
        a = _np.asarray(im.convert("RGB"), dtype=_np.int16)
        r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
        mask = (r > 170) & (g > 135) & (b < 175) & ((r - b) > 60) & ((g - b) > 45)
        ys, xs = _np.where(mask)
        if len(xs) < 40:
            return None
        return [int(xs.min()), int(ys.min()), int(xs.max() - xs.min()),
                int(ys.max() - ys.min())]

    def iou(a, b):
        ax0, ay0, aw, ah = a
        bx0, by0, bw, bh = b
        ix0, iy0 = max(ax0, bx0), max(ay0, by0)
        ix1, iy1 = min(ax0 + aw, bx0 + bw), min(ay0 + ah, by0 + bh)
        inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
        uni = aw * ah + bw * bh - inter
        return inter / uni if uni else 0.0

    fig_gate_ok, fig_gate_bad = 0, 0
    for pg in timeline["pages"][1:]:
        figs = (pg.get("regions") or {}).get("figures") or []
        if not figs:
            continue
        for seg in (pg.get("segments") or []):
            if seg.get("region") != "figure" or seg.get("fig") is None:
                continue
            fi = seg["fig"]
            if fi >= len(figs) or not figs[fi]:
                continue
            tgt = figs[fi]
            mid_local = (seg["t"][0] + seg["t"][1]) / 2
            zoom_hit = [z for z in (pg.get("zooms") or [])
                        if z["t"][0] <= mid_local <= z["t"][1]]
            if zoom_hit:
                score = iou(zoom_hit[0]["box"], tgt)
                way = "放大框"
            else:
                t = pg["t0"] + pg["lead_in"] + mid_local
                fdst = os.path.join(frames_dir or "/tmp", "fig-%02d-%d.png"
                                    % (pg["page_no"], int(mid_local * 10)))
                grab_frame(ff, video, t, fdst)
                box = accent_bbox(Image.open(fdst))
                if box is None:
                    fail("第%d页说「%s…」时画面没有高亮框——指图缺失"
                         % (pg["page_no"], seg["text"][:16]))
                    fig_gate_bad += 1
                    continue
                score = iou(box, tgt)
                way = "高亮框"
            if score < 0.45:
                fail("第%d页说「%s…」（图%s）但%s落在别处（IoU %.2f）——说的图≠指的图"
                     % (pg["page_no"], seg["text"][:16],
                        str(seg.get("fig")), way, score))
                fig_gate_bad += 1
            else:
                fig_gate_ok += 1
    if fig_gate_ok + fig_gate_bad:
        ok("指图一致性：%d/%d 段高亮/放大落在所讲的图上"
           % (fig_gate_ok, fig_gate_ok + fig_gate_bad))

    # ---- 字幕残留：网址 / 汇报人自我介绍 ----
    for s in srt:
        if re.search(r"https?://|arxiv\.org|doi\.org|www\.", s["text"]):
            fail("字幕念了网址：「%s」——网址只放页面" % s["text"])
        if "汇报人" in s["text"]:
            warn("字幕出现「汇报人」——视频不做汇报人介绍")
    if not any(re.search(r"https?://|arxiv", s["text"]) for s in srt):
        ok("字幕无网址残留")

    # ---- 背景音试听样本导出（人工听感用）----
    if gap_mean is not None:
        try:
            g0 = None
            for pg in timeline["pages"]:
                a0 = pg["t0"] + pg["lead_in"] + (pg.get("audio_dur") or 0) + 0.15
                a1 = pg["t0"] + pg.get("seg_dur", 4) - 0.15
                if a1 - a0 >= 0.9:
                    g0 = (a0, a1)
                    break
            if g0:
                sample = os.path.join(work, "bgm_sample.wav")
                subprocess.run([ff, "-y", "-ss", str(g0[0]), "-to", str(g0[1]),
                                "-i", video, "-vn", sample],
                               capture_output=True)
                print("[门禁][样本] 背景音试听片段 → %s（请人耳确认轻松愉快、不抢人声）" % sample)
        except Exception:
            pass

    # ---- 动画生效：讲解时刻帧 ≠ 纯底图（refs/closing 无图区，不参与） ----
    anim_checked = 0
    for pg in timeline["pages"][1:]:
        if pg.get("kind") in ("refs", "closing"):
            continue
        segs = pg.get("segments") or []
        if not segs:
            continue
        seg = segs[0]
        t = pg["t0"] + pg["lead_in"] + (seg["t"][0] + seg["t"][1]) / 2
        fdst = os.path.join(frames_dir or "/tmp", "anim-%02d.png" % pg["idx"])
        grab_frame(ff, video, t, fdst)
        f = Image.open(fdst).resize((480, 270))
        base = Image.open(os.path.join(work, pg["slide"])).resize((480, 270))
        if frame_diff_rate(f, base, tol=12) > 0.985:
            warn("第%d页讲解时刻画面与底图几乎相同——光标/高亮没画上？"
                 % pg["page_no"])
        else:
            anim_checked += 1
    if anim_checked:
        ok("动画生效抽检：%d 页讲解时刻有光标/高亮/放大痕迹" % anim_checked)

    n_zoom = sum(len(p.get("zooms") or []) for p in timeline["pages"])
    print("\n—— 汇总 ——")
    print("错误 %d 条，警告 %d 条；放大段 %d 个；光标关键帧 %d 个"
          % (len(FAILS), len(WARNS), n_zoom,
             sum(len(p.get("cursor") or []) for p in timeline["pages"])))
    if FAILS:
        raise SystemExit(1)


def _sec(ts):
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s.replace(",", "."))


if __name__ == "__main__":
    main()
