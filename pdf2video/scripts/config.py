#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdf2video 模板配置加载。

固化项（背景音乐配方、主播音、音量、字幕字体、封面模板）写在 config.example.toml；
想长期改默认就复制成 config.toml（已被 .gitignore 忽略，不进仓库）。
所有脚本共享这里的读取逻辑：--config 显式指定 > 技能目录 config.toml >
config.example.toml > 代码内置默认。
"""

import os
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)

DEFAULTS = {
    "voice": {  # 主播音：女声。backend=cosyvoice 走本地 CosyVoice2（更自然），
                # backend=edge 走 edge-tts 兜底；下面 name/rate/pitch 是 edge 参数
        "backend": "cosyvoice",
        "name": "zh-CN-XiaoxiaoNeural",
        "rate": "-8%",
        "pitch": "+2Hz",
        "volume": "+0%",
        "cosy": {   # CosyVoice2（CPU 推理；模型缺失/RTF 超限自动退回 edge）
            "model_dir": "",      # 留空按常见路径自动发现（~/models/CosyVoice2-0.5B 等）
            "speaker": "中文女",   # sft 模式内置说话人；cross 模式留空走克隆
            "prompt_voice": "zh-CN-XiaoyiNeural",  # 用户选定的默认音色：晓伊克隆（cosy2_xiaoyi）
            "speed": 1.0,
            "threads": 96,
        },
    },
    "bgm": {    # 背景音乐：固定配方确定性生成（每次一致）；填 path 则用现成音频
        "path": "",
        "seed": 20260914,
        "bpm": 72,
        "loop_minutes": 1.0,
        "gain_db": -15.0,   # 相对人声的混音电平（明显可闻，闪避保证不抢）
        "duck_db": -9.0,    # 有人声时侧链压低量
    },
    "mix": {
        "narration_gain_db": 0.0,
        "limiter": 0.95,
    },
    "subtitle": {  # 解说旁白字幕
        "font": "Noto Sans CJK SC",
        "size": 15,             # ASS PlayRes 相对字号
        "primary": "&H00FFFFFF&",
        "outline": "&H00202020&",
        "margin_v": 30,
        "max_chars": 18,        # 每条字幕字数上限
    },
    "video": {
        "fps": 30,
        "crf": 18,
        "preset": "veryfast",
        "lead_in": 0.8,         # 每页旁白前留白（光标入场+呼吸）
        "lead_out": 1.3,        # 每页旁白后留白（收尾）
        "fade": 0.5,            # 页间黑场淡入淡出单边时长
        "zoom_min_sec": 4.0,    # 讲图时长超过它才做局部放大
    },
    "cover": {  # 视频封面模板（B 站风格，脚本按模板绘制）
        "badge": "论文解读",
        "bg": "deep",           # deep=深蓝科技 / light=浅色清新
        "accent": "#FFD54A",    # 高亮标记色
        "duration_sec": 4.5,    # 开场封面停留时长（无开场白音频时）
        "opening_line": "今天用几分钟，带大家读懂这篇论文。",
    },
}

# 女声白名单：check_video 按它核对「主播音必须是女声且自然」
FEMALE_VOICES = {
    "zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural",
    "zh-CN-liaoning-XiaobeiNeural", "zh-CN-shaanxi-XiaoniNeural",
    "zh-HK-HiuGaaiNeural", "zh-HK-HiuMaanNeural",
    "zh-TW-HsiaoChenNeural", "zh-TW-HsiaoYuNeural",
}


def _deep_merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path=None):
    """返回合并后的配置 dict；找不到 TOML 就用内置默认。"""
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    candidates = []
    if path:
        candidates.append(path)
    candidates.append(os.path.join(SKILL_DIR, "config.toml"))
    candidates.append(os.path.join(SKILL_DIR, "config.example.toml"))
    for c in candidates:
        if c and os.path.exists(c):
            with open(c, "rb") as f:
                data = tomllib.load(f)
            _deep_merge(cfg, data)
            break
    return cfg


def font_path():
    """字幕/封面可用的中文字体（优先黑体，封面标题用 Black 重字重）。"""
    cands = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def black_font_path():
    cands = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return font_path()


_FONT_INDEX_CACHE = {}


def load_font(path, size):
    """加载 .ttc 里的简体中文字面（Noto CJK ttc 第 0 面是 JP，字形有日式写法）。"""
    from PIL import ImageFont
    if path not in _FONT_INDEX_CACHE:
        idx = 0
        for i in range(8):
            try:
                f = ImageFont.truetype(path, 16, index=i)
                if "SC" in (f.getname()[1] or ""):
                    idx = i
                    break
            except Exception:
                break
        _FONT_INDEX_CACHE[path] = idx
    return ImageFont.truetype(path, size, index=_FONT_INDEX_CACHE[path])
