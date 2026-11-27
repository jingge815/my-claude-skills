#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按模板配方生成轻快背景音乐（确定性：同配方同种子，每次输出完全一致）。

不走联网下载、不依赖外部素材：C 大调 I–V–vi–IV 进行，软垫和弦 + 稀疏五声
拨音，bpm 72。整体是「背景」定位——低频少、峰值 0.5、无打击乐，混音时再
压到人声底下（[mix] gain_db / duck_db），不抢解说。

配方固化在 config [bgm]；换音乐给 bgm.path 塞一个现成 mp3/wav 即可，
本脚本只在无 path 时被调用。同一配方产物缓存在技能 assets/ 下，全库复用
同一首（先选好背景音乐，后面都用这个）。

用法：
    python3 make_bgm.py --out <视频工作目录>/bgm/bgm.wav
    python3 make_bgm.py --out bgm.wav --seed 3 --bpm 80      # 换风格试听用
"""

import argparse
import json
import os
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cfgmod  # noqa: E402

SR = 44100

# 和弦进行（I–V–vi–IV ×2 收尾回 I），音名到频率
NOTE_FREQ = {
    name: 440.0 * 2 ** ((i - 9) / 12)
    for i, name in enumerate(
        ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])
}


def freq(note, octave):
    semis = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[note]
    return 440.0 * 2 ** ((semis - 9) / 12 + (octave - 4))


# 每个和弦：低音根音 + 垫音声位（音名，八度）
PROGRESSION = [
    ("C", [[ "C", 4], ["E", 4], ["G", 4], ["B", 4]]),   # Cmaj7
    ("G", [["B", 3], ["D", 4], ["G", 4], ["A", 4]]),   # G add9
    ("A", [["A", 3], ["C", 4], ["E", 4], ["G", 4]]),   # Am7
    ("F", [["A", 3], ["C", 4], ["F", 4], ["G", 4]]),   # Fmaj7(add9)
    ("C", [["C", 4], ["E", 4], ["G", 4], ["B", 4]]),
    ("G", [["B", 3], ["D", 4], ["G", 4], ["A", 4]]),
    ("A", [["A", 3], ["C", 4], ["E", 4], ["G", 4]]),
    ("F", [["A", 3], ["C", 4], ["F", 4], ["G", 4]]),
    ("C", [["D", 4], ["E", 4], ["G", 4], ["B", 4]]),   # 收在 Cmaj9，循环回 C 不突兀
]
PENTA = [("C", 5), ("D", 5), ("E", 5), ("G", 5), ("A", 5)]


def pad_voice(freq_hz, n, rng):
    """软垫单音：基频+两个低幅度泛音，双声部微失谐做宽度。"""
    t = np.arange(n) / SR
    det = 1.0 + rng.uniform(-0.0015, 0.0015)
    w = (np.sin(2 * np.pi * freq_hz * t)
         + 0.38 * np.sin(2 * np.pi * freq_hz * 2 * det * t)
         + 0.12 * np.sin(2 * np.pi * freq_hz * 3 * det * t))
    return w / 1.5


def env_ar(n, attack, release):
    e = np.ones(n)
    a = min(int(attack * SR), n // 2)
    r = min(int(release * SR), n // 2)
    e[:a] = np.linspace(0, 1, a) ** 2
    e[-r:] *= np.linspace(1, 0, r) ** 1.5
    return e


def make_loop(seed, bpm, loop_minutes):
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    chord_len = 8 * beat                       # 每和弦 8 拍
    n_chords = max(1, int(round(loop_minutes * 60 / chord_len / len(PROGRESSION)))) * len(PROGRESSION)
    loop_len = n_chords * chord_len            # 精确对齐，循环点无缝
    n_loop = int(round(loop_len * SR))
    mixL = np.zeros(n_loop)
    mixR = np.zeros(n_loop)
    ov = int(2.0 * SR)                         # 和弦间重叠渐变

    for i in range(n_chords):
        root, voicing = PROGRESSION[i % len(PROGRESSION)]
        t0 = int(round(i * chord_len * SR))
        n_seg = min(n_loop - t0 + ov, int((chord_len + 2.0) * SR))
        seg_n = n_seg
        env = env_ar(seg_n, attack=1.4, release=2.0)
        segL = np.zeros(seg_n)
        segR = np.zeros(seg_n)
        # 低音根音（C2 附近，柔和正弦）
        fb = freq(root, 2)
        tb = np.arange(seg_n) / SR
        bass = np.sin(2 * np.pi * fb * tb) * env_ar(seg_n, 0.8, 1.5) * 0.28
        segL += bass
        segR += bass * 0.9
        # 垫音：双声部微失谐分左右
        for k, (nm, oc) in enumerate(voicing):
            f = freq(nm, oc)
            vL = pad_voice(f, seg_n, rng) * env * (0.55 - 0.05 * k)
            vR = pad_voice(f * 1.0012, seg_n, rng) * env * (0.55 - 0.05 * k)
            pan = 0.45 + 0.1 * (k % 2)
            segL += vL * pan
            segR += vR * (1.0 - pan)
        # 稀疏拨音：五声音阶，隔拍 55% 概率，慢包络（背景点缀，不抢戏）
        for b in range(0, 8, 2):
            if rng.random() < 0.55:
                nm, oc = PENTA[rng.integers(len(PENTA))]
                f = freq(nm, oc)
                pt0 = int(round((i * chord_len + b * beat) * SR))
                pt = pt0 - t0
                if pt < 0 or pt + int(1.1 * SR) > seg_n:
                    continue   # 拨音包络放不完会切出爆点，宁可不排这一拍
                pn = int(1.1 * SR)
                if pn <= 0:
                    continue
                tp = np.arange(pn) / SR
                pluck = (np.sin(2 * np.pi * f * tp)
                         + 0.2 * np.sin(2 * np.pi * f * 2 * tp))
                pluck *= np.exp(-tp * 3.2) * 0.16
                segL[pt:pt + pn] += pluck * 0.9
                segR[pt:pt + pn] += pluck * 0.5
        # 重叠渐变写进 loop（越界部分回绕到开头，保证循环点无缝）
        end = min(t0 + seg_n, n_loop)
        mixL[t0:end] += segL[:end - t0]
        mixR[t0:end] += segR[:end - t0]
        if t0 + seg_n > n_loop:
            w0 = end - t0
            wl = t0 + seg_n - n_loop
            mixL[:wl] += segL[w0:w0 + wl]
            mixR[:wl] += segR[w0:w0 + wl]

    # 归一化到峰值 0.5；振荡分量自身无直流，只去掉包络残留的直流偏置
    peak = max(np.abs(mixL).max(), np.abs(mixR).max())
    mixL, mixR = mixL / peak * 0.5, mixR / peak * 0.5
    mixL -= mixL.mean()
    mixR -= mixR.mean()
    return np.stack([mixL, mixR], axis=1), loop_len


def main():
    ap = argparse.ArgumentParser(description="生成模板背景音乐（确定性配方）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", help="覆盖模板配置的 toml")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--bpm", type=float)
    args = ap.parse_args()

    cfg = cfgmod.load(args.config)
    seed = args.seed if args.seed is not None else int(cfg["bgm"]["seed"])
    bpm = args.bpm if args.bpm else float(cfg["bgm"]["bpm"])
    loop_minutes = float(cfg["bgm"]["loop_minutes"])

    audio, loop_len = make_loop(seed, bpm, loop_minutes)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    pcm = (audio * 32767).astype(np.int16)
    with wave.open(args.out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    meta = {"seed": seed, "bpm": bpm, "loop_seconds": round(loop_len, 3),
            "duration": round(len(audio) / SR, 3)}
    json.dump(meta, open(os.path.splitext(args.out)[0] + ".json", "w"),
              ensure_ascii=False, indent=1)
    print("BGM：%.1fs 循环（seed=%d bpm=%g）→ %s"
          % (meta["duration"], seed, bpm, args.out))


if __name__ == "__main__":
    main()
