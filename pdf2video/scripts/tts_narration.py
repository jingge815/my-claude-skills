#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐页旁白 TTS：合成每页解说音频，并记录词级时间戳（字幕与动画的时间基准）。

- 主播音走模板配置（config [voice]：女声、rate/pitch 微调去机器感）；
- 每页一次整段合成（句间停顿交给语音引擎，语调比逐句拼接自然）；
- WordBoundary 词级时间戳落到 tts.json，字幕切行后按词对齐，音字同步不靠估。
- 顺带做 TTS 文本清洗：去掉 content.json 可能残留的强调标记，避免念出符号。

用法：
    python3 tts_narration.py --content content.json --out <视频工作目录>
    python3 tts_narration.py --content c.json --out . --only 3,7   # 只重合成这几页
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cfgmod  # noqa: E402
import num_norm  # noqa: E402

MARKUP_RE = re.compile(r"\{\{|\}\}|\*\*|`+")
WS_RE = re.compile(r"\s+")
CLAUSE_SPLIT = re.compile(r"(?<=[，、：])")


def clean_for_tts(text):
    """TTS 输入清洗：强调标记、引用标记、数字读法归一化、空白。

    返回 (读法文本, [(原文, 读法), ...])——映射进 tts.json 供数字溯源核对。
    """
    text = MARKUP_RE.sub("", text)
    text = WS_RE.sub(" ", text).strip()
    return num_norm.clean_for_tts(text)


def _clause_units(text, off, dur):
    """把一句（真实时长已知）拆成分句词单元：句内按字数比例分配时间。

    词文本去标点——build_timeline 的 align_words 按原文跳标点对齐。
    """
    sents = [s for s in re.split(r"(?<=[。！？；])", text) if s.strip()]
    total_chars = sum(len(re.sub(r"[，、：。！？；\s]", "", s)) for s in sents) or 1
    words, cursor = [], off
    for s in sents:
        core = re.sub(r"[，、：。！？；\s]", "", s)
        if not core:
            continue
        sdur = dur * len(core) / total_chars
        clauses = [c for c in CLAUSE_SPLIT.split(s) if c.strip()] or [s]
        cchars = [len(re.sub(r"[，、：。！？；\s]", "", c)) for c in clauses]
        csum = sum(cchars) or 1
        t0 = cursor
        for c, n in zip(clauses, cchars):
            cc = re.sub(r"[，、：。！？；\s]", "", c)
            if not cc:
                continue
            cd = sdur * n / csum
            words.append({"t": cc, "off": round(t0, 3), "dur": round(cd, 3)})
            t0 += cd
        cursor += sdur
    return words


async def synth_page(text, voice, out_mp3, page_no, attempts=3):
    import edge_tts
    last_err = None
    for i in range(attempts):
        try:
            comm = edge_tts.Communicate(
                text, voice["name"], rate=voice["rate"],
                pitch=voice["pitch"], volume=voice["volume"],
                boundary="WordBoundary")
            audio, words = [], []
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    audio.append(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    words.append({"t": chunk["text"],
                                  "off": round(chunk["offset"] / 1e7, 3),
                                  "dur": round(chunk["duration"] / 1e7, 3)})
            if not audio:
                raise RuntimeError("合成返回空音频")
            with open(out_mp3, "wb") as f:
                f.write(b"".join(audio))
            return words
        except Exception as e:  # 网络抖动重试
            last_err = e
            await asyncio.sleep(2 * (i + 1))
    raise SystemExit("TTS 合成失败（第%d页）：%s" % (page_no, last_err))


def probe_dur(ff, path):
    import subprocess
    r = subprocess.run([ff, "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        raise SystemExit("读不到音频时长：%s" % path)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def main():
    ap = argparse.ArgumentParser(description="逐页旁白 TTS（含词级时间戳）")
    ap.add_argument("--content", required=True)
    ap.add_argument("--out", required=True, help="视频工作目录")
    ap.add_argument("--config", help="覆盖模板配置的 toml")
    ap.add_argument("--voice-name", help="临时换主播音（如 zh-CN-XiaoyiNeural）")
    ap.add_argument("--only", help="只重合成这些页，如 3,7")
    args = ap.parse_args()

    cfg = cfgmod.load(args.config)
    voice = dict(cfg["voice"])
    if args.voice_name:
        voice["name"] = args.voice_name

    content = json.load(open(args.content))
    narration = content["narration"]
    if isinstance(narration, list):
        narration = {str(i + 1): t for i, t in enumerate(narration)}
    n_pages = len(content["pages"])

    out_dir = os.path.abspath(args.out)
    audio_dir = os.path.join(out_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()

    backend = cfg.get("backend", "cosyvoice")
    cosy_result = None
    if backend == "cosyvoice":
        from cosyvoice_backend import find_repo_python
        repo_py = cfg.get("cosy", {}).get("python") or find_repo_python()
        # prompt_voice 进 job，供 cosyvoice_backend 生成克隆提示音
        if not repo_py or not os.path.exists(repo_py):
            print("[TTS] 找不到 CosyVoice 环境，退回 edge-tts")
            backend = "edge"
        else:
            # 整批走子进程作业（CosyVoice 独立 venv，一次装载整批合成）
            content = json.load(open(args.content))
            narration_all = content["narration"]
            if isinstance(narration_all, list):
                narration_all = {str(i + 1): t for i, t in enumerate(narration_all)}
            job = {"pages": [{"no": n, "text": clean_for_tts(narration_all.get(str(n), ""))[0]}
                             for n in range(1, n_pages + 1)],
                   "out_dir": out_dir,
                   "speaker": cfg.get("cosy", {}).get("speaker", "中文女"),
                   "threads": int(cfg.get("cosy", {}).get("threads", 96)),
                   "speed": float(cfg.get("cosy", {}).get("speed", 1.0)),
                   "model_dir": cfg.get("cosy", {}).get("model_dir") or None,
                   "prompt_voice": cfg.get("cosy", {}).get("prompt_voice",
                                                          "zh-CN-XiaoyiNeural")}
            job_path = os.path.join(out_dir, "tts_job.json")
            json.dump(job, open(job_path, "w"), ensure_ascii=False)
            print("CosyVoice 批量合成启动（后台约 %d-%d 分钟，见 tts_cosy.json 进度）"
                  % (n_pages * 2, n_pages * 5))
            r = subprocess.run([repo_py, os.path.join(HERE, "cosyvoice_backend.py"),
                                "--job", job_path])
            if r.returncode != 0:
                print("[TTS] CosyVoice 作业失败（rc=%d），退回 edge-tts" % r.returncode)
                backend = "edge"
            else:
                cosy_result = json.load(open(os.path.join(out_dir, "tts_cosy.json")))
                print("CosyVoice 批量合成完成：%d 页" % len(cosy_result["pages"]))

    tts_path = os.path.join(out_dir, "tts.json")
    tts = json.load(open(tts_path)) if os.path.exists(tts_path) else {"voice": voice, "pages": {}}
    tts["voice"] = voice

    only = None
    if args.only:
        only = {int(x) for x in re.split(r"[,\s]+", args.only) if x.strip().isdigit()}

    total_chars = 0
    for no in range(1, n_pages + 1):
        if only and no not in only:
            total_chars += len(tts["pages"].get(str(no), {}).get("text", ""))
            continue
        text = narration.get(str(no), "")
        if not text:
            # 空旁白页：补一段静音，保持时间轴完整
            tts["pages"][str(no)] = {"text": "", "audio": None, "dur": 0.0, "words": []}
            print("第%2d页：旁白为空（跳过）" % no)
            continue
        if cosy_result is not None:
            res = cosy_result["pages"].get(str(no)) or {}
            if not res or not res.get("audio"):
                # 批量作业里跳过的空旁白页
                tts["pages"][str(no)] = {"text": text, "audio": None,
                                         "dur": 0.0, "words": []}
                print("第%2d页：旁白为空（跳过）" % no)
                continue
            words = []
            for st, off, dur in res["sentences"]:
                words.extend(_clause_units(st, off, dur))
            tts["pages"][str(no)] = {
                "text": text, "spoken": clean_for_tts(text)[0],
                "audio": res["audio"], "dur": res["dur"], "words": words,
                "backend": "cosyvoice",
                "last_word_end": round(words[-1]["off"] + words[-1]["dur"], 3) if words else 0.0,
            }
        else:
            spoken, num_map = clean_for_tts(text)
            mp3 = os.path.join(audio_dir, "page%02d.mp3" % no)
            words = asyncio.run(synth_page(spoken, voice, mp3, no))
            dur = probe_dur(ff, mp3)
            tts["pages"][str(no)] = {
                "text": text, "spoken": spoken,
                "audio": os.path.basename(mp3),
                "dur": round(dur, 3), "words": words,
                "num_map": num_map,
                "last_word_end": round(words[-1]["off"] + words[-1]["dur"], 3) if words else 0.0,
            }
        total_chars += len(text)
        print("第%2d页：%d 字，音频 %.1fs，%d 个词单元"
              % (no, len(text), float(tts["pages"][str(no)].get("dur") or 0), len(words)))

    json.dump(tts, open(tts_path, "w"), ensure_ascii=False, indent=1)
    print("TTS 完成：%d 字，输出 %s" % (total_chars, tts_path))


if __name__ == "__main__":
    main()
