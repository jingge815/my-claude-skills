"""旁白文本处理：切分字幕句、发音替换、时间对齐。tts_video.py 使用。"""
from __future__ import annotations

import re

from common import plain

MAX_SUB_CHARS = 24   # 单条字幕最大显示宽度（按中文字计，英文字母算半个）
MIN_SUB_CHARS = 6

_STRONG = "。！？；!?;"
_WEAK = "，、：,:）)"
_NORM_RE = re.compile(r"[0-9A-Za-z一-鿿]")


def disp_len(s: str) -> float:
    n = 0.0
    for ch in s:
        n += 0.5 if ord(ch) < 128 else 1.0
    return n


def _split_keep(text: str, seps: str) -> list[str]:
    out, buf = [], ""
    for ch in text:
        buf += ch
        if ch in seps:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return [s for s in out if s.strip()]


def _hard_split(s: str, limit: float) -> list[str]:
    """按长度硬切，尽量不切断英文单词。"""
    tokens = re.findall(r"[A-Za-z0-9_.%\-+/]+|\s+|.", s)
    out, buf = [], ""
    for t in tokens:
        if buf and disp_len(buf + t) > limit:
            out.append(buf)
            buf = t.lstrip()
        else:
            buf += t
    if buf.strip():
        out.append(buf)
    return out


def split_subtitles(text: str, limit: float = MAX_SUB_CHARS) -> list[str]:
    """把一页旁白切成若干条字幕，每条不超过 limit 个中文字宽。"""
    text = re.sub(r"\s+", " ", plain(text)).strip()
    chunks = []
    for sent in _split_keep(text, _STRONG):
        if disp_len(sent) <= limit:
            chunks.append(sent.strip())
            continue
        # 先按弱标点切，再把相邻小段尽量拼回去
        parts = []
        for p in _split_keep(sent, _WEAK):
            parts.extend(_hard_split(p, limit) if disp_len(p) > limit else [p])
        buf = ""
        for p in parts:
            if buf and disp_len(buf + p) > limit:
                chunks.append(buf.strip())
                buf = p
            else:
                buf += p
        if buf.strip():
            chunks.append(buf.strip())
    # 合并过短的尾巴
    merged = []
    for c in chunks:
        if merged and disp_len(c) < MIN_SUB_CHARS and disp_len(merged[-1] + c) <= limit + 2:
            merged[-1] += c
        else:
            merged.append(c)
    return merged


def display_sub(s: str) -> str:
    """字幕显示时去掉句末的弱标点，看起来更干净。"""
    return s.rstrip("，、；：,;:。 ").strip() or s


# 默认读法规则：字幕保留符号，送给 TTS 的文本换成口语读法
_SPOKEN_RULES = [
    (re.compile(r"(\d)\s*[×xX]\s*(\d)"), r"\1乘\2"),     # 16×16 → 16乘16
    (re.compile(r"(\d(?:\.\d+)?)\s*×"), r"\1倍"),          # 26× → 26倍
    (re.compile(r"[~～∼]\s*(?=\d)"), "约"),                  # ~14 → 约14
    (re.compile(r"(\d)\s*[–—~-]\s*(\d)"), r"\1到\2"),       # 52–96 → 52到96
    (re.compile(r"(?<=[\u4e00-\u9fff])\s*/\s*(?=[\u4e00-\u9fff])"), "或"),
]


def apply_pron(text: str, pron: dict[str, str]) -> str:
    """发音替换：只影响送给 TTS 的文本，不影响字幕显示。先用户词表（长词优先），再默认规则。"""
    for k in sorted(pron or {}, key=len, reverse=True):
        text = text.replace(k, pron[k])
    for pat, rep in _SPOKEN_RULES:
        text = pat.sub(rep, text)
    return text


def norm_len(s: str) -> int:
    return len(_NORM_RE.findall(s))


def align_chunks(chunk_tts_texts: list[str], words: list[tuple[float, float, str]], audio_dur: float):
    """把字幕段与 TTS 的词边界对齐，返回每段 (start, end) 秒。

    words: [(offset_s, duration_s, text)]，来自 edge-tts WordBoundary。
    对齐失败时退化为按字数比例分配。
    """
    n = len(chunk_tts_texts)
    lens = [max(1, norm_len(t)) for t in chunk_tts_texts]
    total_c = sum(lens)
    if not words:
        return _proportional(lens, audio_dur)
    cum_w, pos = [], 0
    for off, dur, t in words:
        pos += norm_len(t)
        cum_w.append((pos, off, off + dur))
    total_w = pos
    if total_w == 0 or abs(total_w - total_c) / total_c > 0.25:
        return _proportional(lens, audio_dur)
    ratio = total_w / total_c
    bounds, acc = [], 0
    for L in lens[:-1]:
        acc += L
        target = acc * ratio
        end_t = cum_w[-1][2]
        for p, s0, s1 in cum_w:
            if p >= target - 0.01:
                end_t = s1
                break
        bounds.append(end_t)
    starts = [max(0.0, words[0][0] - 0.05)] + bounds
    ends = bounds + [max(bounds[-1] if bounds else 0, cum_w[-1][2]) + 0.15]
    out = []
    for i in range(n):
        s, e = starts[i], ends[i]
        if e <= s:
            e = s + 0.5
        out.append((s, min(e, audio_dur)))
    return out


def _proportional(lens, dur):
    total = sum(lens)
    out, t = [], 0.0
    for L in lens:
        d = dur * L / total
        out.append((t, t + d))
        t += d
    return out
