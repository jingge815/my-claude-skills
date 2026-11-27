#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CosyVoice2 本地 TTS 后端（CPU 推理，无 GPU 环境的豆包级女声方案）。

- 模型：CosyVoice2-0.5B（约 3.5GB，modelscope 下载，路径自动发现 + config 可覆盖）；
- 推理：torch CPU 多线程（按机器核数配置）；句级合成——旁白按句切开逐句合成，
  每句拿真实音频时长，句内按字数分配给分句（字幕行对齐用）；
- 说话人：优先内置「中文女」（sft 模式），模型无内置说话人时退 cross_lingual
  零样本克隆（提示音走 edge-tts 现生成一次并缓存）；
- B0 验收门：--bench 实测 CPU 合成速率（RTF）。**RTF > 8 视为不可用**，
  调用方退回 edge-tts 后端并在报告注明。
"""

import os
import re
import sys
import wave

import numpy as np

SENT_SPLIT = re.compile(r"(?<=[。！？；])")
MODEL_CANDIDATES = [
    "/data/user_data/jgfeng/models/CosyVoice2-0.5B",
    os.path.expanduser("~/models/CosyVoice2-0.5B"),
    os.path.expanduser("~/CosyVoice2-0.5B"),
]
REPO_CANDIDATES = [
    "/data/user_data/jgfeng/models/CosyVoice",
    os.path.expanduser("~/models/CosyVoice"),
]
RTF_LIMIT = 8.0
SR = 22050


def find_model_dir(cfg_dir=None):
    cands = ([cfg_dir] if cfg_dir else []) + MODEL_CANDIDATES
    for c in cands:
        if c and os.path.exists(os.path.join(c, "llm.pt")):
            return c
    return None


def _setup_repo():
    for r in REPO_CANDIDATES:
        if os.path.exists(os.path.join(r, "cosyvoice", "cli", "cosyvoice.py")):
            for sub in (r, os.path.join(r, "third_party", "Matcha-TTS")):
                if sub not in sys.path:
                    sys.path.insert(0, sub)
            os.chdir(r)          # CosyVoice 内部按相对路径找资源
            return r
    raise SystemExit("找不到 CosyVoice 仓库（git clone FunAudioLLM/CosyVoice）")


class CosyBackend:
    """句级合成后端。load() 一次，synth_sentences() 反复调用。"""

    def __init__(self, model_dir=None, speaker="中文女", speed=1.0, threads=0,
                 prompt_voice="zh-CN-XiaoyiNeural"):
        self.model_dir = find_model_dir(model_dir)
        if not self.model_dir:
            raise SystemExit("找不到 CosyVoice2-0.5B 模型目录（先跑 modelscope 下载）")
        self.speaker = speaker
        self.prompt_voice = prompt_voice   # cross 模式用该 edge 女声生成克隆提示音
        self.speed = speed
        self.model = None
        self.mode = None       # 'sft' | 'cross'
        self.threads = threads

    def load(self):
        import torch
        if self.threads:
            torch.set_num_threads(self.threads)
        _setup_repo()
        import logging
        for noisy in ("transformers", "torch", "matcha"):
            logging.getLogger(noisy).setLevel(logging.ERROR)
        from cosyvoice.cli.cosyvoice import CosyVoice2
        self.model = CosyVoice2(self.model_dir, load_jit=False,
                                load_trt=False, fp16=False)
        speakers = getattr(self.model, "list_available_spks", lambda: [])()
        if self.speaker in (speakers or []):
            self.mode = "sft"
        else:
            self.mode = "cross"
            self._prompt = self._ensure_prompt_wav()
        return self

    def _ensure_prompt_wav(self):
        """cross_lingual 需要提示音：按配置的 edge 女声生成并缓存
        （assets/prompts/<voice>.wav；voice_samples.py 的样例共用同一缓存）。"""
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "assets", "prompts")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "%s.wav" % self.prompt_voice.replace("-", "_"))
        if os.path.exists(path):
            return path
        import asyncio
        import edge_tts
        text = ("今天用几分钟带大家读懂这篇论文。我们从研究背景讲起，"
                "看看这篇工作为什么能把推理速度提上来。")

        async def gen():
            comm = edge_tts.Communicate(text, self.prompt_voice, rate="-8%")
            await comm.save(path)

        asyncio.run(gen())
        return path

    def synth_sentence(self, text):
        """合成一句 → (int16 numpy, sr)。失败返回 None。"""
        import torchaudio
        chunks = []
        if self.mode == "sft":
            gen = self.model.inference_sft(text, self.speaker,
                                           stream=False, speed=self.speed)
        else:
            # CosyVoice2 的 cross_lingual 直接收提示音文件路径（内部自 load/重采样）
            gen = self.model.inference_cross_lingual(
                text, self._prompt, stream=False, speed=self.speed)
        import torch
        for out in gen:
            chunks.append(out["tts_speech"])          # (1, N) float tensor
        if not chunks:
            return None
        a = torch.cat(chunks, dim=1).squeeze(0).numpy()
        return np.clip(a * 32767, -32767, 32767).astype(np.int16)

    def synth_sentences(self, text):
        """按句合成。返回 (全部音频 int16 拼接, [(句文本, 偏移秒, 时长秒), ...])。"""
        sents = [s for s in SENT_SPLIT.split(text) if s.strip()]
        audio_all, times, off = [], [], 0.0
        for s in sents:
            a = self.synth_sentence(s.strip())
            if a is None:
                continue
            dur = len(a) / SR
            times.append((s.strip(), off, dur))
            audio_all.append(a)
            off += dur
        if not audio_all:
            return None, []
        return np.concatenate(audio_all), times


def write_wav(path, audio_int16, sr=SR):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(audio_int16.tobytes())


def bench(model_dir=None, threads=64):
    """B0 验收门：测 CPU 合成速率。返回 (rtf, 说明)。"""
    import time
    t0 = time.time()
    bk = CosyBackend(model_dir=model_dir, threads=threads).load()
    print("装载 %.1fs" % (time.time() - t0))
    text = ("存内计算把计算放进存储颗粒里，注意力层正好长在它的甜点上。"
            "端到端时延降了百分之四十，批量推理的吞吐提升三点九一倍。") * 2
    t1 = time.time()
    audio, times = bk.synth_sentences(text)
    wall = time.time() - t1
    dur = len(audio) / SR
    rtf = wall / max(0.1, dur)
    print("合成 %.1fs 音频耗时 %.1fs → RTF %.2f（限制 %.1f）"
          % (dur, wall, rtf, RTF_LIMIT))
    return rtf, "可用" if rtf <= RTF_LIMIT else "不可用，退回 edge-tts"


def find_repo_python():
    """CosyVoice 自带 venv 的 python（推理依赖都装在那里）。"""
    import glob
    for r in REPO_CANDIDATES:
        for pat in (os.path.join(r, ".venv", "bin", "python"),
                    os.path.join(r, "venv", "bin", "python")):
            if os.path.exists(pat):
                return pat
    for pat in glob.glob("/data/*/models/CosyVoice/.venv/bin/python"):
        return pat
    return None


def run_job(job_path):
    """批量作业：装载一次，合成全部页，写 wav + 时间戳 JSON。

    job = {"pages": [{"no": 1, "text": "..."}, ...],
           "out_dir": "...", "speaker": "中文女", "threads": 96,
           "model_dir": null}
    输出：out_dir/audio/pageNN.wav 与 out_dir/tts_cosy.json
          （pages: {"no": {"dur", "sentences": [[text, off, dur], ...], "backend": "cosyvoice"}}）
    """
    import json as _json
    import time as _time
    job = _json.load(open(job_path))
    out_dir = os.path.abspath(job["out_dir"])
    audio_dir = os.path.join(out_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    bk = CosyBackend(model_dir=job.get("model_dir"),
                     speaker=job.get("speaker", "中文女"),
                     speed=float(job.get("speed", 1.0)),
                     threads=int(job.get("threads", 96)),
                     prompt_voice=job.get("prompt_voice",
                                          "zh-CN-XiaoyiNeural")).load()
    result = {"backend": "cosyvoice", "speaker": bk.speaker, "mode": bk.mode,
              "pages": {}}
    for pg in job["pages"]:
        text = pg["text"]
        if not text.strip():
            result["pages"][str(pg["no"])] = {"dur": 0.0, "sentences": [], "audio": None}
            continue
        t0 = _time.time()
        audio, times = bk.synth_sentences(text)
        if audio is None:
            raise SystemExit("CosyVoice 合成失败（第%d页）" % pg["no"])
        wav = os.path.join(audio_dir, "page%02d.wav" % pg["no"])
        write_wav(wav, audio)
        result["pages"][str(pg["no"])] = {
            "dur": round(len(audio) / SR, 3),
            "sentences": [[t, round(o, 3), round(d, 3)] for t, o, d in times],
            "audio": os.path.basename(wav),
            "rtf": round(_time.time() - t0, 1),
        }
        print("  第%d页 %.1fs 音频（合成 %.0fs）" % (pg["no"], len(audio) / SR,
                                                  _time.time() - t0), flush=True)
    _json.dump(result, open(os.path.join(out_dir, "tts_cosy.json"), "w"),
               ensure_ascii=False, indent=1)
    print("JOB_DONE")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="CosyVoice2 CPU 后端 bench / 批量作业")
    ap.add_argument("--job", help="批量作业 JSON（跨 venv 子进程模式）")
    ap.add_argument("--model-dir")
    ap.add_argument("--threads", type=int, default=64)
    ap.add_argument("--out", help="把样例音频写到这个 wav")
    args = ap.parse_args()
    if args.job:
        run_job(args.job)
        raise SystemExit(0)
    import time
    bk = CosyBackend(model_dir=args.model_dir, threads=args.threads).load()
    t0 = time.time()
    audio, times = bk.synth_sentences(
        "存内计算把计算放进了存储颗粒里。橙色区域是注意力层，红色区域是全连接层。"
        "批量推理的端到端时延降低了百分之四十。")
    wall = time.time() - t0
    print("RTF %.2f（%.1fs 音频 / %.1fs 墙钟）" % (wall / (len(audio) / SR),
                                                  len(audio) / SR, wall))
    for t, off, d in times:
        print("  [%.2f+%.2f] %s" % (off, d, t[:24]))
    if args.out:
        write_wav(args.out, audio)
        print("样例音频 →", args.out)
