#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选音色样例批量生成：同一测试文本 × 多个 TTS 配置 → wav 样例供人耳挑选。

挑选是人工决策（音色自然度无法机器判定），所以样例生成器把候选全部物化成
wav，用户听完点名后把选中配置写进 config [voice.cosy] 固化。

当前候选（--set 控制跑哪批）：
  set=cosy2   CosyVoice2 零样本克隆 × 不同 edge 提示音 × instruct2 语气指令
  set=cosy1   CosyVoice v1 300M 内置「中文女」（需先下载 iic/CosyVoice-300M）
  set=spark   Spark-TTS 0.5B（需先装 SparkAudio/Spark-TTS）
"""

import argparse
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "/data/user_data/jgfeng/models/CosyVoice"
MODEL2 = "/data/user_data/jgfeng/models/CosyVoice2-0.5B"
MODEL1 = "/data/user_data/jgfeng/models/CosyVoice-300M-SFT"

TEXT = ("大家好，今天用几分钟带大家读懂这篇论文。这篇工作的核心是把计算搬进内存条，"
        "端到端时延降低了 342 毫秒，能效提升 2.81 倍。大家看这张图：橙色区域是注意力层，"
        "红色区域是全连接层，批越大，橙色区域占的比例就越高。")


def setup_repo():
    for sub in (REPO, os.path.join(REPO, "third_party", "Matcha-TTS")):
        if sub not in sys.path:
            sys.path.insert(0, sub)
    os.chdir(REPO)


def ensure_prompt_wav(name, edge_voice, text=None):
    """用 edge-tts 现生成克隆提示音（assets/prompts/<name>.wav）。"""
    import asyncio
    import edge_tts
    d = os.path.join(HERE, "..", "assets", "prompts")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s.wav" % name)
    if os.path.exists(path):
        return path
    t = text or ("今天用几分钟带大家读懂这篇论文。我们从研究背景讲起，"
                 "看看这篇工作为什么能把推理速度提上来。")

    async def gen():
        comm = edge_tts.Communicate(t, edge_voice, rate="-8%")
        await comm.save(path)

    asyncio.run(gen())
    return path


def gen_cosy2(samples, out_dir):
    sys.path.insert(0, HERE)
    from cosyvoice_backend import CosyBackend, write_wav
    cases = [
        ("cosy2_xiaoxiao", dict(speaker="", prompt_voice="zh-CN-XiaoxiaoNeural",
                                instruct=None)),
        ("cosy2_xiaoyi", dict(speaker="", prompt_voice="zh-CN-XiaoyiNeural",
                              instruct=None)),
        ("cosy2_xiaoyi_instruct", dict(speaker="", prompt_voice="zh-CN-XiaoyiNeural",
                                       instruct="用自然亲切、略带活泼的语气说，像朋友分享新知识，语速适中")),
        ("cosy2_xiaomo_instruct", dict(speaker="", prompt_voice="zh-CN-XiaomoNeural",
                                       instruct="平静专业，像科普讲解员，娓娓道来")),
    ]
    bk = CosyBackend(model_dir=MODEL2, speaker="中文女", threads=96).load()
    import torchaudio
    for name, c in cases:
        try:
            prompt = ensure_prompt_wav(c["prompt_voice"].replace("-", "_"),
                                       c["prompt_voice"])
            t0 = time.time()
            if c["instruct"]:
                gen = bk.model.inference_instruct2(
                    TEXT, c["instruct"], prompt, stream=False, speed=1.0)
            else:
                gen = bk.model.inference_cross_lingual(
                    TEXT, prompt, stream=False, speed=1.0)
            import torch
            chunks = [out["tts_speech"] for out in gen]
            if not chunks:
                print("  [skip] %s 空音频" % name)
                continue
            a = torch.cat(chunks, dim=1).squeeze(0).numpy()
            write_wav(os.path.join(out_dir, "%s.wav" % name),
                      np.clip(a * 32767, -32767, 32767).astype("int16"))
            print("  [ok] %s.wav（%.1fs 音频 / %.0fs）"
                  % (name, len(a) / 22050, time.time() - t0))
        except Exception as e:
            print("  [fail] %s: %s" % (name, e))


def gen_cosy1(samples, out_dir):
    sys.path.insert(0, HERE)
    from cosyvoice_backend import write_wav, _setup_repo as setup_repo
    setup_repo()
    from cosyvoice.cli.cosyvoice import CosyVoice
    if not os.path.exists(os.path.join(MODEL1, "llm.pt")):
        print("  [skip] CosyVoice-300M 未下载")
        return
    import torch
    try:
        model = CosyVoice(MODEL1, load_jit=False, load_trt=False, fp16=False)
    except Exception as e:
        print("  [fail] CosyVoice-300M 装载失败: %s" % e)
        return
    for spk in ("中文女",):
        t0 = time.time()
        chunks = [out["tts_speech"] for out in
                  model.inference_sft(TEXT, spk, stream=False, speed=1.0)]
        a = torch.cat(chunks, dim=1).squeeze(0).numpy()
        name = "cosy1_%s" % spk
        write_wav(os.path.join(out_dir, "%s.wav" % name),
                  np.clip(a * 32767, -32767, 32767).astype("int16"))
        print("  [ok] %s.wav（%.1fs 音频 / %.0fs）"
              % (name, len(a) / model.sample_rate, time.time() - t0))


def gen_spark(samples, out_dir):
    """Spark-TTS：参数控制女声（无提示音）+ 示例提示音克隆 + 晓伊克隆。"""
    spark_repo = "/data/user_data/jgfeng/models/Spark-TTS"
    if not os.path.exists(spark_repo):
        print("  [skip] Spark-TTS 未安装")
        return
    sys.path.insert(0, spark_repo)
    os.chdir(spark_repo)
    try:
        from cli.SparkTTS import SparkTTS
        import soundfile as sf
    except ImportError as e:
        print("  [skip] Spark-TTS 依赖缺失：%s" % e)
        return
    model_dir = "/data/user_data/jgfeng/models/Spark-TTS-0.5B"
    if not os.path.exists(model_dir):
        print("  [skip] Spark-TTS-0.5B 模型未下载")
        return
    model = SparkTTS(model_dir, "cpu")
    xiaoyi = ensure_prompt_wav("zh-CN-XiaoyiNeural", "zh-CN-XiaoyiNeural")
    cases = [
        ("spark_female_ctl", dict(gender="female", pitch="moderate", speed="moderate")),
        ("spark_prompt", dict(prompt_speech_path=os.path.join(
            spark_repo, "example", "prompt_audio.wav"))),
        ("spark_xiaoyi_clone", dict(prompt_speech_path=xiaoyi)),
    ]
    for name, kw in cases:
        try:
            t0 = time.time()
            audio = model.inference(TEXT, **kw)      # -> numpy（16k）
            arr = audio.squeeze() if hasattr(audio, "squeeze") else audio
            if hasattr(arr, "numpy"):
                arr = arr.numpy()
            sf.write(os.path.join(out_dir, "%s.wav" % name), arr, 16000)
            print("  [ok] %s.wav（%.1fs 音频 / %.0fs）"
                  % (name, len(arr) / 16000, time.time() - t0))
        except Exception as e:
            print("  [fail] %s: %s" % (name, e))


def torchaudio_load(path):
    import torchaudio
    return torchaudio.load(path)


import numpy as np  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="候选音色样例生成")
    ap.add_argument("--set", default="cosy2", help="cosy2 / cosy1 / spark / all")
    ap.add_argument("--out-dir", default="/data/fjg/flagOS/pim-paper/AttAcc_video/voice_samples")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    sets = [args.set] if args.set != "all" else ["cosy2", "cosy1", "spark"]
    if args.set == "all":
        sets = ["cosy2", "cosy1", "spark"]
    for s in sets:
        print("== %s ==" % s)
        {"cosy2": gen_cosy2, "cosy1": gen_cosy1, "spark": gen_spark}[s](None, args.out_dir)
    print("样例目录：%s" % args.out_dir)


if __name__ == "__main__":
    main()
