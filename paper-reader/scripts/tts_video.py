"""旁白配音 + 字幕 + 视频合成。

用法: pr.sh tts_video <content.json> [--engine auto|edge|piper] [--voice V] [--rate +10%]
                      [--out 输出.mp4] [--no-cache]

流程：
  1. 每页旁白切成字幕句；送 TTS 前做发音替换（tts.pron），字幕仍显示原文
  2. edge-tts（在线，音质好，带词边界用于对齐字幕）；失败时退到 piper（离线，按句合成，时间精确）
  3. 每条字幕渲染一帧：幻灯片缩放置顶 + 底部字幕条
  4. ffmpeg 拼接成 1920x1080 H.264 + AAC 的 mp4，同时输出 .srt 外挂字幕

音频按 (引擎, 音色, 语速, 文本) 缓存在 build/tts/，只改了一页旁白时只会重新合成那一页。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw

from common import THEME, die, hex_rgb, load_content, log, pil_font, plain, slug_of
from narrate import align_chunks, apply_pron, display_sub, split_subtitles

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SR = 24000
VIDEO_W, VIDEO_H = 1920, 1080
SLIDE_SCALE = 0.86  # 幻灯片在视频里的缩放比例，下方留出字幕条
LEAD_IN = 0.35       # 每页开头静音
TAIL = 0.65          # 每页结尾静音
DEFAULT_VOICE = {"edge": "zh-CN-YunxiNeural", "piper": "zh_CN-huayan-medium"}
PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/"
PIPER_DIR = Path(os.environ.get("PAPER_READER_PIPER_DIR", Path.home() / ".cache" / "paper-reader" / "piper"))


def run_ffmpeg(args, what):
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        die(f"ffmpeg {what} 失败：{r.stderr.strip()[-800:]}")


def to_wav(src: Path, dst: Path):
    run_ffmpeg(["-i", str(src), "-ac", "1", "-ar", str(SR), "-sample_fmt", "s16", str(dst)], "转码")


def wav_duration(p: Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def read_pcm(p: Path) -> bytes:
    with wave.open(str(p)) as w:
        if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{p} 采样格式不对")
        return w.readframes(w.getnframes())


def silence(sec: float) -> bytes:
    return b"\x00\x00" * int(round(sec * SR))


# ================================================================ TTS 引擎
class EdgeEngine:
    name = "edge"

    def __init__(self, voice, rate):
        import edge_tts  # noqa: F401
        self.voice = voice or DEFAULT_VOICE["edge"]
        self.rate = rate or "+0%"

    async def _synth(self, text, mp3: Path):
        import edge_tts
        com = edge_tts.Communicate(text, self.voice, rate=self.rate, boundary="WordBoundary",
                                   connect_timeout=15, receive_timeout=90)
        words = []
        with open(mp3, "wb") as f:
            async for ch in com.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    words.append((ch["offset"] / 1e7, ch["duration"] / 1e7, ch["text"]))
        return words

    def synth_slide(self, chunks_tts, work: Path):
        """整页一次合成（语调连贯），再用词边界切分字幕时间。"""
        text = "".join(chunks_tts)
        mp3 = work.with_suffix(".mp3")
        last = None
        for attempt in range(3):
            try:
                words = asyncio.run(self._synth(text, mp3))
                if mp3.stat().st_size < 1000:
                    raise RuntimeError("返回音频为空")
                break
            except Exception as e:  # 网络抖动重试
                last = e
        else:
            raise RuntimeError(f"edge-tts 失败：{last}")
        wav = work.with_suffix(".wav")
        to_wav(mp3, wav)
        mp3.unlink(missing_ok=True)
        dur = wav_duration(wav)
        return wav, align_chunks(chunks_tts, words, dur)


class PiperEngine:
    name = "piper"
    GAP = 0.18  # 句间停顿

    def __init__(self, voice, rate):
        from piper import PiperVoice
        model = self._ensure_model(voice or DEFAULT_VOICE["piper"])
        self.voice = PiperVoice.load(model)
        # rate 形如 "+10%"：语速越快 length_scale 越小
        pct = 0
        if rate:
            try:
                pct = int(rate.strip().rstrip("%"))
            except ValueError:
                pct = 0
        from piper.config import SynthesisConfig
        self.cfg = SynthesisConfig(length_scale=max(0.5, 1.0 / (1 + pct / 100.0)))

    @staticmethod
    def _ensure_model(name):
        if name.endswith(".onnx") and Path(name).exists():
            return name
        PIPER_DIR.mkdir(parents=True, exist_ok=True)
        onnx = PIPER_DIR / f"{name}.onnx"
        cfg = PIPER_DIR / f"{name}.onnx.json"
        if not (onnx.exists() and cfg.exists()):
            if name != DEFAULT_VOICE["piper"]:
                die(f"找不到 piper 模型 {onnx}，请手动下载或使用默认音色")
            import urllib.request
            log(f"下载离线语音模型（约 60MB）到 {PIPER_DIR} ...")
            for fn in (onnx, cfg):
                urllib.request.urlretrieve(PIPER_URL + fn.name, fn)
        return str(onnx)

    def synth_slide(self, chunks_tts, work: Path):
        pcm, spans, t = b"", [], 0.0
        tmp = work.with_suffix(".part.wav")
        for i, c in enumerate(chunks_tts):
            with wave.open(str(tmp), "wb") as wf:
                self.voice.synthesize_wav(c, wf, syn_config=self.cfg)
            part = work.with_suffix(".p24.wav")
            to_wav(tmp, part)
            data = read_pcm(part)
            d = len(data) / 2 / SR
            spans.append((t, t + d))
            pcm += data
            t += d
            if i < len(chunks_tts) - 1:
                pcm += silence(self.GAP)
                t += self.GAP
        for p in (tmp, work.with_suffix(".p24.wav")):
            p.unlink(missing_ok=True)
        wav = work.with_suffix(".wav")
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm)
        return wav, spans


def make_engine(kind, voice, rate):
    if kind in ("edge", "auto"):
        try:
            return EdgeEngine(voice if kind == "edge" else (voice if voice and "Neural" in voice else None), rate)
        except ImportError:
            if kind == "edge":
                die("未安装 edge-tts")
    return PiperEngine(voice if voice and "Neural" not in voice else None, rate)


# ================================================================ 字幕帧
def subtitle_frame(slide_png: Path, text: str | None, out: Path):
    canvas = Image.new("RGB", (VIDEO_W, VIDEO_H), hex_rgb(THEME["primary"]))
    with Image.open(slide_png) as im:
        sw, sh = int(VIDEO_W * SLIDE_SCALE), int(VIDEO_H * SLIDE_SCALE)
        canvas.paste(im.convert("RGB").resize((sw, sh), Image.LANCZOS), ((VIDEO_W - sw) // 2, 18))
    if text:
        band_top = 18 + int(VIDEO_H * SLIDE_SCALE)
        d = ImageDraw.Draw(canvas)
        size = 44
        font = pil_font(size, True)
        while font.getlength(text) > VIDEO_W - 120 and size > 28:
            size -= 2
            font = pil_font(size, True)
        cy = band_top + (VIDEO_H - band_top) // 2
        d.text((VIDEO_W // 2, cy), text, font=font, fill=(255, 255, 255), anchor="mm",
               stroke_width=2, stroke_fill=(10, 20, 35))
    canvas.save(out)


def srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ================================================================ 主流程
def build_video(content_path: Path, engine_kind="auto", voice=None, rate=None, out=None, use_cache=True,
                frames_dir: Path | None = None):
    content_path = Path(content_path).resolve()
    content = load_content(content_path)
    slides = content["slides"]
    tts_cfg = content.get("tts", {})
    voice = voice or tts_cfg.get("voice")
    rate = rate or tts_cfg.get("rate")
    pron = tts_cfg.get("pron", {})
    base = content_path.parent
    frames_dir = frames_dir or base / "build" / "frames"
    missing = [i for i in range(1, len(slides) + 1) if not (frames_dir / f"slide{i:02d}.png").exists()]
    if missing:
        die(f"缺少幻灯片帧 {missing}，请先运行 render_frames")
    tts_dir = base / "build" / "tts"
    sub_dir = base / "build" / "subframes"
    tts_dir.mkdir(parents=True, exist_ok=True)
    sub_dir.mkdir(parents=True, exist_ok=True)
    for old in sub_dir.glob("*.png"):
        old.unlink()

    engine = make_engine(engine_kind, voice, rate)
    log(f"TTS 引擎：{engine.name}（音色 {getattr(engine, 'voice', '') if engine.name == 'edge' else voice or DEFAULT_VOICE['piper']}）")

    all_pcm = b""
    t_global = 0.0
    segments = []  # (frame_png, duration)
    srt = []
    timeline = []
    for i, s in enumerate(slides, 1):
        narration = plain(s.get("narration", "")).strip()
        slide_png = frames_dir / f"slide{i:02d}.png"
        if not narration:
            dur = float(s.get("duration", 3.0))
            all_pcm += silence(dur)
            segments.append((slide_png, dur))
            timeline.append({"slide": i, "start": round(t_global, 2), "duration": round(dur, 2)})
            t_global += dur
            continue
        chunks = split_subtitles(narration)
        chunks_tts = [apply_pron(c, pron) for c in chunks]
        key = hashlib.sha1(json.dumps([engine.name, str(voice), str(rate), chunks_tts],
                                      ensure_ascii=False).encode()).hexdigest()[:16]
        work = tts_dir / f"slide{i:02d}_{key}"
        cache_meta = work.with_suffix(".json")
        if use_cache and cache_meta.exists() and work.with_suffix(".wav").exists():
            wav, spans = work.with_suffix(".wav"), [tuple(x) for x in json.loads(cache_meta.read_text())]
        else:
            for old in tts_dir.glob(f"slide{i:02d}_*"):
                old.unlink()
            try:
                wav, spans = engine.synth_slide(chunks_tts, work)
            except Exception as e:
                if engine.name == "edge" and engine_kind == "auto":
                    log(f"edge-tts 不可用（{e}），切换到离线 piper")
                    engine = PiperEngine(None, rate)
                    wav, spans = engine.synth_slide(chunks_tts, work)
                else:
                    die(f"第 {i} 页语音合成失败：{e}")
            cache_meta.write_text(json.dumps(spans))
        pcm = read_pcm(wav)
        adur = len(pcm) / 2 / SR
        page_dur = LEAD_IN + adur + TAIL
        all_pcm += silence(LEAD_IN) + pcm + silence(TAIL)

        # 字幕帧与时间轴：段与段之间无缝衔接，页首/页尾无字幕
        cursor = 0.0
        sub_frames = []
        for k, ((st, en), text) in enumerate(zip(spans, chunks)):
            st_p = LEAD_IN + st
            en_p = LEAD_IN + (spans[k + 1][0] if k + 1 < len(spans) else en)
            if st_p > cursor + 0.01:
                sub_frames.append((None, st_p - cursor))
                cursor = st_p
            sub_frames.append((display_sub(text), en_p - cursor))
            srt.append((t_global + st_p, t_global + LEAD_IN + en, display_sub(text)))
            cursor = en_p
        if page_dur > cursor + 0.01:
            sub_frames.append((None, page_dur - cursor))
        blank_png = sub_dir / f"s{i:02d}_blank.png"
        for k, (text, d) in enumerate(sub_frames):
            if text is None:
                if not blank_png.exists():
                    subtitle_frame(slide_png, None, blank_png)
                segments.append((blank_png, d))
            else:
                p = sub_dir / f"s{i:02d}_{k:03d}.png"
                subtitle_frame(slide_png, text, p)
                segments.append((p, d))
        timeline.append({"slide": i, "start": round(t_global, 2), "duration": round(page_dur, 2),
                         "chars": len(narration)})
        t_global += page_dur
        log(f"第 {i}/{len(slides)} 页：{adur:.1f}s，{len(chunks)} 条字幕")

    # 音频
    audio = base / "build" / "narration.wav"
    with wave.open(str(audio), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(all_pcm)

    # 视频（concat demuxer，每帧指定时长；最后一帧需重复一次）
    lst = base / "build" / "frames.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p, d in segments:
            f.write(f"file '{p.as_posix()}'\nduration {d:.3f}\n")
        f.write(f"file '{segments[-1][0].as_posix()}'\n")
    out = Path(out) if out else base / "output" / f"{slug_of(content)}_video.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    log("编码视频 ...")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio),
                "-vf", "fps=25,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage",
                "-crf", "20", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                "-shortest", str(out)], "视频编码")
    srt_path = out.with_suffix(".srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        for n, (a, b, t) in enumerate(srt, 1):
            f.write(f"{n}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n\n")
    (base / "build" / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=1),
                                                   encoding="utf-8")
    log(f"视频已生成：{out}（{t_global / 60:.1f} 分钟）；字幕：{srt_path}")
    return out, srt_path, timeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("content")
    ap.add_argument("--engine", default="auto", choices=["auto", "edge", "piper"])
    ap.add_argument("--voice")
    ap.add_argument("--rate")
    ap.add_argument("--out")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    build_video(Path(a.content), a.engine, a.voice, a.rate, a.out, not a.no_cache)


if __name__ == "__main__":
    main()
