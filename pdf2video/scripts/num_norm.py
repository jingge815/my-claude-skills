#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TTS 数字读法归一化：阿拉伯数字 → 中文口语读法。

用户实测问题：342 被逐位念成「3 4 2」，正确读法是「三百四十二」。
本模块只改「读法文本」（喂给 TTS 的），不碰 content.json 原文——
每个替换记录 (原文, 读法) 存进 tts.json 的 num_map 供溯源核对：
替换前后的数字个数必须一致，一个都不能丢。

规则：
  整数 342 → 三百四十二（含千分位逗号先剥）
  小数 2.81 → 二点八一
  百分比 40% → 百分之四十
  倍数 3.91×/x → 三点九一倍
  年份 2024 年 / 于2024年 → 二零二四（四位且上下文带「年」按位读）
  引用标记 [2] → 删除（TTS 不念文献编号）
"""
import re

DIG_CN = "零一二三四五六七八九"
UNITS = ["", "十", "百", "千"]


def int_to_cn(n: int) -> str | None:
    """整数→中文读法，支持 0..99999999；超范围返回 None（原样保留）。"""
    if n < 0 or n >= 100_000_000:
        return None
    if n == 0:
        return "零"

    def under_10000(v: int) -> str:
        out, zero = "", False
        for i, c in enumerate(f"{v:04d}"):
            d, u = int(c), UNITS[3 - i]
            if d == 0:
                if out:
                    zero = True
                continue
            if zero:
                out += "零"
                zero = False
            if d == 1 and u == "十" and not out:
                out += "十"                 # 10–19 读「十」不读「一十」
            else:
                out += DIG_CN[d] + u
        return out or "零"

    w, r = divmod(n, 10000)
    parts = []
    if w:
        parts.append(under_10000(w) + "万")
        if 0 < r < 1000:
            parts.append("零")              # 万位组与不足千的尾数之间补「零」
    if r:
        parts.append(under_10000(r))
    return "".join(parts)


def num_to_cn(num_str: str) -> str | None:
    """整数或小数 → 中文读法。"""
    if "." in num_str:
        a, b = num_str.split(".", 1)
        ia = int_to_cn(int(a)) if a else "零"
        ib = "".join(DIG_CN[int(c)] for c in b if c.isdigit())
        if ia is None or not ib:
            return None
        return f"{ia}点{ib}"
    return int_to_cn(int(num_str))


_YEAR_RE = re.compile(r"(?:(?<=于)|(?<=在)|(?<=公元))?(19|20)\d{2}(?=\s*年)")
_SPEC_RE = re.compile(r"(?<![\d.])(\d{1,4})\s*[×xX]\s*(\d{1,4})(?![\d.])")
_PCT_RE = re.compile(r"(?<![\d.])\d{1,6}(?:\.\d+)?\s*[%％]")
_MUL_RE = re.compile(r"(?<![\d.])\d{1,6}(?:\.\d+)?\s*[×xX](?![\wA-Za-z0-9])")
_DEC_RE = re.compile(r"(?<![\d.])\d{1,6}\.\d+(?![\d%×xX])")
_INT_RE = re.compile(r"(?<![\d.])\d{1,7}(?![\d.%×xX])")
CITE_RE = re.compile(r"\s*\[\d{1,3}\]")


def to_speech(text: str):
    """返回 (读法文本, [(原文, 读法), ...])。只改读法，原文数字一个不丢。"""
    mapping = []
    # 千分位逗号先剥（15,000 → 15000），后面的规则按纯数字匹配
    out = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)

    def rep(pat, make):
        nonlocal out

        def _r(m):
            spoken = make(m)
            if spoken is None:
                return m.group(0)
            mapping.append((m.group(0), spoken))
            return spoken

        out = pat.sub(_r, out)

    rep(_YEAR_RE, lambda m: "".join(DIG_CN[int(c)] for c in m.group(0)))
    rep(_SPEC_RE, lambda m: (num_to_cn(m.group(1)) or m.group(1)) + "乘"
        + (num_to_cn(m.group(2)) or m.group(2)))
    rep(_PCT_RE, lambda m: "百分之" + (num_to_cn(m.group(0)[:-1].strip()) or m.group(0)))
    rep(_MUL_RE, lambda m: (num_to_cn(m.group(0)[:-1].strip()) or m.group(0)[:-1]) + "倍")
    rep(_DEC_RE, lambda m: num_to_cn(m.group(0)) or m.group(0))
    rep(_INT_RE, lambda m: num_to_cn(m.group(0)) or m.group(0))
    return out, mapping


def clean_for_tts(text: str):
    """TTS 输入清洗：剥引用标记 + 数字归一化。返回 (读法文本, 映射)。"""
    t = CITE_RE.sub("", text)
    return to_speech(t)


if __name__ == "__main__":
    tests = [
        "端到端时延降了 342 毫秒，能效提升 2.81×，命中率 40%。",
        "发表于 2024 年，批量 128 时吞吐 1.95 倍。",
        "见 AttAcc[2] 的设计，3.91× 加速比，0.62 的归一化时延。",
        "表 3 列出 18 个 bank、3×128 的阵列，共 15,000 次实验。",
    ]
    for t in tests:
        sp, mp = to_speech(t)
        print(sp, "|", mp)
