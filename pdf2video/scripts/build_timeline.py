#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""时间轴编排：旁白词级时间戳 + 槽位几何 → timeline.json + subtitles.srt。

这是「声音、字幕、画面」三路对齐的枢纽：
  1. 字幕：按标点把旁白切成 ≤18 字的行，用词级时间戳对齐（音字同步，不靠估）；
  2. 讲解段：按句号聚成段，每段根据内容分派讲解区域（讲图指图、讲结论指总结条）；
  3. 动画：光标关键帧（段落切换时滑过去、到位点击脉冲）、长讲图段做局部放大；
  4. 页序：封面卡（视频开场）+ PPT 各页，逐页 lead_in/lead_out 拼成总时间轴。

分派规则是启发式，产物 timeline.json 可手工微调（契约见 references/timeline-spec.md），
改完直接重跑 render_video.py，不用重新 TTS。

用法：
    python3 build_timeline.py --content content.json --work <视频工作目录> \
        --ppt-work ../<PDF名>_ppt --config x.toml
"""

import argparse
import asyncio
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cfgmod  # noqa: E402

_TOKEN_RE = re.compile(r"[0-9A-Za-z!?+.%×]+|.")   # 连续 ASCII 不拆开

PUNCT = "。！？；：，、,.!?;:（）()《》\"\"''—…·"
SENT_END = "。！？；"
MAX_CHARS_DEFAULT = 18

FIGURE_RE = re.compile(r"图|表|截图|柱|曲线|横轴|纵轴|流程|框架|架构|示例|框图|饼图|热力|矩阵|示意|面板|左|右|上|下")
# 颜色区域描述段（"橙色区域是注意力层"）几乎必然在讲图——核对 agent 实测的漏判模式
COLOR_FIG_RE = re.compile(r"[红橙黄绿蓝紫青灰粉深]色?[区域部分柱线三角框块点]|颜色越|颜色代表|色块")
LINK_RE = re.compile(r"网址|链接|arXiv|DOI|下载|开源")
SUMMARY_RE = re.compile(r"总结|回顾|综上|以上就是|要点|启发|不足|改进|感谢聆听")
TAKEAWAY_RE = re.compile(r"一句话|记住|核心|take-?away", re.I)


def split_lines(text, max_chars):
    """按标点层级切字幕行：先句读，超长子句再按逗号/顿号补刀。"""
    lines = []
    for sent in re.split(r"(?<=[。！？；])", text):
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) <= max_chars:
            lines.append(sent)
            continue
        # 超长句按逗号顿号再切，还超长的按等宽硬切
        parts = re.split(r"(?<=[，、：,])", sent)
        buf = ""
        for p in parts:
            if not p:
                continue
            if buf and len(buf + p) > max_chars:
                if len(buf) <= max_chars:
                    lines.append(buf)
                    buf = p
                else:
                    lines.extend(_hard_cut(buf, max_chars))
                    buf = p
            else:
                buf += p
        if buf:
            if len(buf) <= max_chars:
                lines.append(buf)
            else:
                lines.extend(_hard_cut(buf, max_chars))
    return lines


_JIEBA = None


def _cut_tokens(text):
    """词元：jieba 分词（中文不拆词），ASCII 连串再合并成原子。"""
    global _JIEBA
    if _JIEBA is None:
        try:
            import jieba
            jieba.setLogLevel(60)
            _JIEBA = jieba
        except ImportError:
            _JIEBA = False
    # jieba 的词元（含多字词与 ASCII 词）原样保留——不能再经单字符正则拆开，
    # 否则"共享"会被拆成"共/享"，断词白做
    toks = (_JIEBA.cut(text) if _JIEBA else _TOKEN_RE.findall(text))
    return [t for t in toks if t.strip()]


def _hard_cut(text, max_chars):
    """超长无标点子句硬切：按词元切，中文词与 ASCII 连串（Transformer/3.91×）不拆。"""
    out, cur = [], ""
    for tok in _cut_tokens(text):
        if cur and len(cur + tok) > max_chars:
            out.append(cur)
            cur = tok.lstrip()
        else:
            cur += tok
    if cur:
        out.append(cur)
    return out


def align_words(text, words):
    """把词级时间戳对回原文：给每个词标上原文 char 区间。"""
    ti = 0
    out = []
    for w in words:
        while ti < len(text) and text[ti] in PUNCT + " 　":
            ti += 1
        seg = text[ti:ti + len(w["t"])]
        if seg != w["t"]:
            return None  # 对不齐（数字读法等），退回按字数比例
        out.append((ti, ti + len(w["t"]), w))
        ti += len(w["t"])
    return out


def line_times(lines, text, aligned, words, audio_dur):
    """每行字幕的时间窗；对不齐时按字数比例铺满有效语音区间。"""
    spans = []
    pos = 0
    for ln in lines:
        s = text.find(ln, pos)
        s = s if s >= 0 else pos
        spans.append((s, s + len(ln)))
        pos = s + len(ln)
    if aligned:
        times = []
        for ls, le in spans:
            ws = [(o, o2, w) for o, o2, w in aligned if o < le and o2 > ls]
            if ws:
                st = ws[0][2]["off"]
                en = ws[-1][2]["off"] + ws[-1][2]["dur"]
            else:
                st = en = None
            times.append([st, en])
        # 无词行（纯标点残留）用相邻行补
        for i, t in enumerate(times):
            if t[0] is None:
                left = next((times[j][1] for j in range(i - 1, -1, -1) if times[j][1]), 0.0)
                right = next((times[j][0] for j in range(i + 1, len(times)) if times[j][0]), left + 1.0)
                t[0], t[1] = left, max(left + 0.6, right)
        # 行间不重叠
        for i in range(1, len(times)):
            if times[i][0] < times[i - 1][1]:
                mid = (times[i - 1][1] + times[i][0]) / 2 if times[i][0] > times[i - 1][1] else times[i - 1][1]
                times[i - 1][1] = mid
                times[i][0] = mid
        return times
    # 兜底：按字数比例
    n_chars = max(1, sum(len(ln) for ln in lines))
    voice_end = max((w["off"] + w["dur"]) for w in words) if words else audio_dur * 0.95
    t = 0.0
    times = []
    for ln in lines:
        d = (len(ln) / n_chars) * voice_end
        times.append([t, t + d])
        t += d
    return times


def pick_figure(seg_text, n_figs):
    if n_figs <= 0:
        return None
    if n_figs == 1:
        return 0
    if re.search(r"左|上|第一|首先", seg_text):
        return 0
    if re.search(r"右|下|第二", seg_text):
        return 1
    if re.search(r"第三", seg_text):
        return min(2, n_figs - 1)
    return 0


def fig_meta_list(page):
    """content.json 页面的 figure 元数据（ref/brief/axes），按放置顺序。"""
    f = page.get("figure")
    items = f if isinstance(f, list) else ([f] if f else [])
    out = []
    for it in items:
        it = it if isinstance(it, dict) else {}
        out.append({"ref": str(it.get("ref") or ""),
                    "brief": str(it.get("brief") or it.get("caption") or ""),
                    "axes": str(it.get("axes") or "")})
    return out


REF_NUM_RE = re.compile(r"[图表]\s*(\d{1,2})")
POS_RE = re.compile(r"左上|右上|左下|右下|左半|右半|左|右|上|下|中间|中心")


def ref_number(ref):
    m = re.search(r"(\d{1,2})", ref or "")
    return int(m.group(1)) if m else None


def pick_figure_v2(seg_text, figs_meta, n_figs):
    """指图分派 v2：编号精确匹配 → brief/axes 词面重合 → 位置词 → 默认第 0 张。"""
    if n_figs <= 0:
        return None
    # 1) 旁白里的「图 15 / 表 3」精确对 ref
    for m in REF_NUM_RE.finditer(seg_text):
        want = int(m.group(1))
        for i, meta in enumerate(figs_meta[:n_figs]):
            if ref_number(meta["ref"]) == want and meta["ref"]:
                return i
    # 2) brief/axes 词面重合（≥2 个共同实词）
    seg_words = set(re.findall(r"[\u4e00-\u9fff]{2,6}", seg_text))
    best, best_hit = None, 0
    for i, meta in enumerate(figs_meta[:n_figs]):
        hay = set(re.findall(r"[\u4e00-\u9fff]{2,6}", meta["brief"] + meta["axes"]))
        hit = len(seg_words & hay)
        if hit > best_hit:
            best, best_hit = i, hit
    if best is not None and best_hit >= 2:
        return best
    # 3) 位置词兜底
    return pick_figure(seg_text, n_figs)


def fig_point(seg_text, box):
    """光标在图框内的落点：位置词映射九宫格，缺省中心。"""
    x0, y0, w, h = box
    grid = {
        "左上": (0.28, 0.28), "右上": (0.72, 0.28),
        "左下": (0.28, 0.72), "右下": (0.72, 0.72),
        "左半": (0.25, 0.5), "右半": (0.75, 0.5),
        "左": (0.25, 0.5), "右": (0.75, 0.5),
        "上": (0.5, 0.25), "下": (0.5, 0.75),
        "中间": (0.5, 0.5), "中心": (0.5, 0.5),
    }
    m = POS_RE.search(seg_text)
    fx, fy = grid.get(m.group(0), (0.5, 0.5)) if m else (0.5, 0.5)
    return int(x0 + w * fx), int(y0 + h * fy)


def classify(seg_text, kind, n_figs, figs_meta=None):
    """讲解段 → 讲解区域（figure 时用 ref 精确匹配优先）。"""
    if kind == "refs":
        return "body", None
    if kind == "closing":
        if TAKEAWAY_RE.search(seg_text):
            return "summary", None
        return "summary" if SUMMARY_RE.search(seg_text) else "body", None
    if LINK_RE.search(seg_text):
        return "body", None
    if kind == "cover":
        if re.search(r"截图|首页|右|题目|作者|出处", seg_text) and n_figs:
            return "figure", 0
        return "body", None
    if FIGURE_RE.search(seg_text) and n_figs:
        return "figure", pick_figure_v2(seg_text, figs_meta or [], n_figs)
    if COLOR_FIG_RE.search(seg_text) and n_figs:
        return "figure", pick_figure_v2(seg_text, figs_meta or [], n_figs)
    if SUMMARY_RE.search(seg_text):
        return "summary", None
    return "body", None


def ease(u):
    """smoothstep 缓动。"""
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def main():
    ap = argparse.ArgumentParser(description="生成视频时间轴与字幕")
    ap.add_argument("--content", required=True)
    ap.add_argument("--work", required=True, help="视频工作目录")
    ap.add_argument("--ppt-work", help="pdf2ppt 工作目录（读 slots.json 兜底）")
    ap.add_argument("--config")
    args = ap.parse_args()

    cfg = cfgmod.load(args.config)
    vc, sub_c, vcfg = cfg["video"], cfg["subtitle"], cfg["video"]
    max_chars = int(sub_c.get("max_chars", MAX_CHARS_DEFAULT))
    work = os.path.abspath(args.work)

    content = json.load(open(args.content))
    tts = json.load(open(os.path.join(work, "tts.json")))
    slots = json.load(open(os.path.join(work, "slots.json")))
    slots_by_page = {p["page"]: p for p in slots["pages"]}

    lead_in, lead_out = float(vcfg.get("lead_in", 0.8)), float(vcfg.get("lead_out", 1.3))
    fade = float(vcfg.get("fade", 0.5))
    zoom_min = float(vcfg.get("zoom_min_sec", 4.0))

    # ---- 开场封面卡（视频封面 + 开场白） ----
    pages = []
    srt_lines = []
    cover_png = os.path.join(work, "cover", "cover_2x.png")
    cover_meta_p = os.path.join(work, "cover", "cover_meta.json")
    if os.path.exists(cover_png) and os.path.exists(cover_meta_p):
        meta = json.load(open(cover_meta_p))
        opening = (cfg["cover"].get("opening_line") or "").strip()
        audio_dir = os.path.join(work, "audio")
        os.makedirs(audio_dir, exist_ok=True)
        cov_audio = os.path.join(audio_dir, "cover_open.mp3")
        cov_dur = 0.0
        cov_words = []
        if opening:
            from tts_narration import clean_for_tts, synth_page, probe_dur
            import imageio_ffmpeg as _iff
            voice = tts.get("voice", cfg["voice"])
            spoken_open, _ = clean_for_tts(opening)
            if not os.path.exists(cov_audio):
                cov_words = asyncio.run(synth_page(spoken_open, voice, cov_audio, 0))
            cov_dur = probe_dur(_iff.get_ffmpeg_exe(), cov_audio)
        cover_lines = split_lines(opening, max_chars) if opening else []
        cov_times = line_times(cover_lines, opening, align_words(opening, cov_words),
                               cov_words, cov_dur)
        dur = max(float(cfg["cover"].get("duration_sec", 4.5)),
                  (cov_dur if opening else 0) + 0.6 + 1.0)
        pages.append({
            "idx": 0, "page_no": 0, "kind": "video-cover", "t0": 0.0,
            "title": meta.get("main_title", content.get("title", "")),
            "slide": "cover/cover_2x.png", "audio": "audio/cover_open.mp3",
            "audio_dur": round(cov_dur, 3),
            "lead_in": 0.6, "lead_out": max(1.0, dur - cov_dur - 0.6),
            "ken_burns": True,
            "lines": [{"text": ln, "t": [round(cov_times[i][0] + 0.6, 2),
                                         round(cov_times[i][1] + 0.6, 2)],
                       "page": 0} for i, ln in enumerate(cover_lines)],
            "segments": [], "regions": {"title": None, "body": None, "summary": None,
                                        "figures": []},
            "cursor": [], "zooms": [],
        })
        for i, ln in enumerate(cover_lines):
            srt_lines.append({"page": 0, "text": ln,
                              "start": round(0.6 + cov_times[i][0], 2),
                              "end": round(0.6 + cov_times[i][1], 2)})
        print("开场封面卡：%.1fs，开场白 %d 行字幕" % (dur, len(cover_lines)))

    # ---- PPT 各页 ----
    t = pages[0]["lead_in"] + pages[0]["audio_dur"] + pages[0]["lead_out"] if pages else 0.0
    n_pages = len(content["pages"])
    for no in range(1, n_pages + 1):
        pg = content["pages"][no - 1]
        kind = pg.get("kind", "text-left")
        tt = tts["pages"].get(str(no), {})
        audio = tt.get("audio")
        audio_dur = float(tt.get("dur") or 0.0)
        words = tt.get("words") or []
        text = tt.get("text") or ""
        slide = "slides/%s" % slots["slides"][no - 1]
        regs = slots_by_page[no]["regions"]
        n_figs = len(regs.get("figures") or [])

        lines = split_lines(text, max_chars)
        # 纯标点行（硬切残留的孤立逗号等）没有可念内容，丢弃——
        # 逐字还原校验按去标点比对，不受影响
        lines = [ln for ln in lines if re.search(r"[\w\u4e00-\u9fff]", ln)]
        figs_meta = fig_meta_list(pg)
        aligned = align_words(text, words)
        times = line_times(lines, text, aligned, words, audio_dur)

        # 讲解段：按句末标点聚行
        segments = []
        buf = []
        for i, ln in enumerate(lines):
            buf.append(i)
            if (ln and ln[-1] in SENT_END) or i == len(lines) - 1:
                seg_text = "".join(lines[j] for j in buf)
                st = times[buf[0]][0]
                en = times[buf[-1]][1]
                region, fig_i = classify(seg_text, kind, n_figs, figs_meta)
                if region == "figure" and fig_i is None:
                    region = "body"
                segments.append({"t": [round(st, 2), round(en, 2)],
                                 "text": seg_text, "region": region,
                                 "fig": fig_i if region == "figure" else None})
                buf = []

        # 局部放大：长讲图段。放大框约束：16:9、完整包含图区（外扩 24px）、
        # 不与其他区域相交（否则框边会把正文文字切成半截）、不出画布；
        # 居中放不下就左右锚定，都不行就放弃放大（保留高亮）。
        zooms = []

        def _fits(box, others, contain):
            bx0, by0, bw, bh = box
            if bx0 < -1 or by0 < -1 or bx0 + bw > 1921 or by0 + bh > 1081:
                return False
            if (bx0 > contain[0] + 2 or by0 > contain[1] + 2
                    or bx0 + bw < contain[2] - 2 or by0 + bh < contain[3] - 2):
                return False   # 必须完整包含图区+余量
            for ox0, oy0, ox1, oy1 in others:
                if (bx0 + 12 < ox1 and ox0 + 12 < bx0 + bw
                        and by0 + 12 < oy1 and oy0 + 12 < by0 + bh):
                    return False
            return True

        for seg in segments:
            if not (seg["region"] == "figure" and seg["fig"] is not None
                    and seg["t"][1] - seg["t"][0] >= zoom_min
                    and regs["figures"] and regs["figures"][seg["fig"]]):
                continue
            b = regs["figures"][seg["fig"]]
            PAD = 24
            contain = (max(0, b[0] - PAD), max(0, b[1] - PAD),
                       min(1920, b[0] + b[2] + PAD), min(1080, b[1] + b[3] + PAD))
            zh = max(contain[3] - contain[1], (contain[2] - contain[0]) * 9 / 16.0)
            zw = zh * 16 / 9.0
            if zw >= 1918:
                continue
            others = []
            for name in ("body", "title", "summary"):
                r = regs.get(name)
                if r:
                    others.append((r[0], r[1], r[0] + r[2], r[1] + r[3]))
            for fi, fb in enumerate(regs["figures"]):
                if fi != seg["fig"] and fb:
                    others.append((fb[0], fb[1], fb[0] + fb[2], fb[1] + fb[3]))
            cy_ = (contain[1] + contain[3]) / 2
            cands = [
                (contain[0], cy_ - zh / 2),                                # 左锚定
                (contain[2] - zw, cy_ - zh / 2),                           # 右锚定
                ((contain[0] + contain[2]) / 2 - zw / 2, cy_ - zh / 2),    # 居中
            ]
            box = None
            for cx0, cy0 in cands:
                c0 = (min(max(0.0, cx0), 1920 - zw), min(max(0.0, cy0), 1080 - zh))
                if _fits((c0[0], c0[1], zw, zh), others, contain):
                    box = c0
                    break
            if box is None:
                continue
            zt0 = max(0.0, seg["t"][0] - 0.15)
            zt1 = min(audio_dur + lead_in + lead_out, seg["t"][1] + 0.35)
            zooms.append({"t": [round(zt0, 2), round(zt1, 2)],
                          "box": [round(box[0], 1), round(box[1], 1),
                                  round(zw, 1), round(zh, 1)]})
        # 相邻同框放大段合并（间隙 <1.2s 视为连续讲述同一张图），避免来回推拉
        merged = []
        for z in zooms:
            if merged and z["t"][0] - merged[-1]["t"][1] < 1.2 \
                    and all(abs(a - b) < 2 for a, b in zip(z["box"], merged[-1]["box"])):
                merged[-1]["t"][1] = z["t"][1]
            else:
                merged.append(z)
        zooms = merged

        # 光标关键帧：入场 + 段间滑动 + 到位点击
        cursor = []
        seg_dur = lead_in + audio_dur + lead_out
        cx, cy = 1980, 760
        enter_t = max(0.1, lead_in - 0.45)

        def region_box(region, fig_i):
            if region == "figure" and fig_i is not None and fig_i < len(regs["figures"]):
                return regs["figures"][fig_i]
            if region == "summary" and regs.get("summary"):
                return regs["summary"]
            if region == "title" and regs.get("title"):
                return regs["title"]
            if regs.get("body"):
                return regs["body"]
            if regs.get("title"):
                return regs["title"]
            return None

        def target_point(text, region, fig_i):
            """光标落点：图区域按位置词九宫格，其他区域取中心。"""
            b = region_box(region, fig_i)
            if not b:
                return 960, 540
            if region == "figure":
                return fig_point(text, b)
            return int(b[0] + b[2] / 2), int(b[1] + b[3] / 2)

        if segments:
            # 分句级光标：句内再按逗号顿号切分句（时长按字数比例），
            # 目标变了且分句够长才移动——粒度细但不乱跳
            clauses = []
            for seg in segments:
                st, en = seg["t"]
                cs = [c for c in re.split(r"(?<=[，、；])", seg["text"]) if c.strip()] or [seg["text"]]
                tot = sum(len(c) for c in cs) or 1
                t0 = st
                for c in cs:
                    d = (en - st) * len(c) / tot
                    region, fig_i = classify(c, kind, n_figs, figs_meta)
                    if region == "figure" and fig_i is None:
                        region = "body"
                    clauses.append({"t": [t0, t0 + d], "text": c,
                                    "region": region, "fig": fig_i if region == "figure" else None})
                    t0 += d
            tx, ty = target_point(clauses[0]["text"], clauses[0]["region"], clauses[0].get("fig"))
            cursor.append({"t": round(enter_t, 2), "x": cx, "y": cy, "click": False})
            cursor.append({"t": round(enter_t + 0.65, 2), "x": tx, "y": ty, "click": True})
            px, py = tx, ty
            for cl in clauses[1:]:
                tx, ty = target_point(cl["text"], cl["region"], cl.get("fig"))
                st, en = cl["t"]
                if abs(tx - px) + abs(ty - py) > 40 and en - st >= 1.5:
                    mv = min(1.0, max(0.45, (en - st) * 0.35))
                    cursor.append({"t": round(st, 2), "x": px, "y": py, "click": False})
                    cursor.append({"t": round(st + mv, 2), "x": tx, "y": ty, "click": True})
                    px, py = tx, ty
        cursor.append({"t": round(seg_dur - 0.35, 2), "x": cursor[-1]["x"] if cursor else 1980,
                       "y": cursor[-1]["y"] if cursor else 760, "click": False, "fade_out": True})

        # 页级时间
        t0 = t
        for i, (ln, tt2) in enumerate(zip(lines, times)):
            srt_lines.append({"page": no, "text": ln,
                              "start": round(t0 + lead_in + tt2[0], 2),
                              "end": round(t0 + lead_in + tt2[1], 2)})
        pages.append({
            "idx": len(pages), "page_no": no, "kind": kind,
            "title": pg.get("title", ""), "slide": slide,
            "audio": ("audio/" + audio) if audio else None,
            "audio_dur": round(audio_dur, 3),
            "lead_in": lead_in, "lead_out": lead_out,
            "t0": round(t0, 2), "seg_dur": round(seg_dur, 2),
            "lines": [{"text": ln, "t": [round(t0 + lead_in + tt2[0], 2),
                                         round(t0 + lead_in + tt2[1], 2)],
                       "page": no} for ln, tt2 in zip(lines, times)],
            "segments": segments, "regions": regs,
            "cursor": cursor, "zooms": zooms,
        })
        t += seg_dur
        print("第%2d页（%-12s）音频%.1fs %d行字幕 %d段 %d个放大"
              % (no, kind, audio_dur, len(lines), len(segments), len(zooms)))

    total = round(t, 2)
    # 字幕非空检查
    srt_lines.sort(key=lambda x: x["start"])
    srt_path = os.path.join(work, "subtitles.srt")
    with open(srt_path, "w") as f:
        for i, s in enumerate(srt_lines, 1):
            f.write("%d\n%s --> %s\n%s\n\n" % (
                i, _ts(s["start"]), _ts(s["end"]), _disp(s["text"])))

    timeline = {
        "fps": int(vcfg.get("fps", 30)), "size": [1920, 1080],
        "total_dur": total, "fade": fade,
        "subtitle_style": {k: sub_c.get(k) for k in
                           ("font", "size", "primary", "outline", "margin_v")},
        "srt": "subtitles.srt", "srt_count": len(srt_lines),
        "pages": pages,
    }
    tl_path = os.path.join(work, "timeline.json")
    json.dump(timeline, open(tl_path, "w"), ensure_ascii=False, indent=1)
    print("时间轴：%s（总时长 %.1fs，%d 页，%d 行字幕）"
          % (tl_path, total, len(pages), len(srt_lines)))


_TRAIL_PUNCT = "，。！？；：、,.!?;:"


def _disp(text):
    """字幕显示文本：剥行尾标点（广播字幕惯例），行内标点保留。"""
    return text.rstrip(_TRAIL_PUNCT)


def _ts(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


if __name__ == "__main__":
    main()
