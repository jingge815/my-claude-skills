"""paper-reader 公共工具：路径、字体、主题色、富文本解析、文本测量与折行。

所有排版尺寸单位统一为英寸（inch），字号单位为磅（pt）。
PPT 与视频帧共用同一套测量和折行逻辑，保证两边看起来一致。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

SKILL_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- 主题
SLIDE_W, SLIDE_H = 13.333, 7.5  # 16:9
RENDER_DPI = 144  # 13.333in * 144 = 1920px

THEME = {
    "primary": "1F3A5F",   # 深蓝：标题、封面底色
    "accent": "2E86AB",    # 蓝：强调文字、标题竖条
    "highlight": "D9822B",  # 橙：结论框
    "text": "222222",
    "muted": "6B7280",
    "line": "D5DCE4",
    "soft": "F2F5F9",      # 浅底：表头、图框
    "warm": "FDF3E7",      # 结论框底色
    "white": "FFFFFF",
}

# PPT 里写入的字体名（Windows/Mac 上的 Office 会用它们渲染）
PPT_FONT_EA = "微软雅黑"
PPT_FONT_LATIN = "Arial"

# 本机渲染视频帧用的字体（用于测量与绘制）
_FONT_CANDIDATES = {
    False: [
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),
        ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", 2),
        ("/System/Library/Fonts/PingFang.ttc", 0),
        ("C:/Windows/Fonts/msyh.ttc", 0),
    ],
    True: [
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),
        ("/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", 2),
        ("/System/Library/Fonts/PingFang.ttc", 0),
        ("C:/Windows/Fonts/msyhbd.ttc", 0),
    ],
}


def die(msg: str, code: int = 1):
    print(f"[paper-reader] 错误: {msg}", file=sys.stderr)
    sys.exit(code)


def log(msg: str):
    print(f"[paper-reader] {msg}", file=sys.stderr, flush=True)


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def find_font_file(bold: bool = False) -> tuple[str, int]:
    for path, idx in _FONT_CANDIDATES[bold]:
        if Path(path).exists():
            return path, idx
    try:  # fontconfig 兜底
        style = ":style=Bold" if bold else ""
        out = subprocess.run(
            ["fc-match", "-f", "%{file}|%{index}", f"Noto Sans CJK SC{style}"],
            capture_output=True, text=True, check=True).stdout
        path, idx = out.split("|")
        return path, int(idx)
    except Exception:
        die("找不到中文字体（需要 Noto Sans CJK / 思源黑体 / 微软雅黑 之一）")


@lru_cache(maxsize=None)
def pil_font(px: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path, idx = find_font_file(bold)
    return ImageFont.truetype(path, max(1, int(px)), index=idx)


# ---------------------------------------------------------------- 富文本
# 约定：**加粗强调** 在 PPT/视频里显示为加粗+强调色；旁白与字幕会去掉标记。
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def parse_rich(text: str) -> list[tuple[str, bool]]:
    """把 'a **b** c' 解析成 [('a ', False), ('b', True), (' c', False)]。"""
    runs, pos = [], 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > pos:
            runs.append((text[pos:m.start()], False))
        runs.append((m.group(1), True))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], False))
    return [r for r in runs if r[0]]


def plain(text: str) -> str:
    return _BOLD_RE.sub(r"\1", text or "")


# ---------------------------------------------------------------- 测量与折行
_MEASURE_PX = 200  # 在大字号下测量再按比例缩放，减少取整误差
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-.%+/:'’×~#@&=<>\[\]()]+|\s+|.", re.S)
# 不能出现在行首的标点
_NO_LINE_START = set("，。、；：！？）》」』】,.;:!?)]}%·…—")


def text_width(text: str, size_pt: float, bold: bool = False) -> float:
    """返回文本宽度（英寸）。"""
    if not text:
        return 0.0
    w_px = pil_font(_MEASURE_PX, bold).getlength(text)
    return w_px / _MEASURE_PX * size_pt / 72.0


def line_height(size_pt: float, spacing: float = 1.15) -> float:
    """单行高度（英寸）。PowerPoint 单倍行距约等于 1.2 倍字号。"""
    return size_pt * 1.2 * spacing / 72.0


def wrap_runs(runs: list[tuple[str, bool]], size_pt: float, width_in: float):
    """把富文本按宽度折行。返回 [[(text, bold), ...], ...]，每个内层列表是一行。"""
    tokens: list[tuple[str, bool]] = []
    for text, bold in runs:
        for t in _TOKEN_RE.findall(text):
            tokens.append((t, bold))

    lines: list[list[tuple[str, bool]]] = [[]]
    cur_w = 0.0
    for tok, bold in tokens:
        if tok == "\n":
            lines.append([])
            cur_w = 0.0
            continue
        w = text_width(tok, size_pt, bold)
        if cur_w + w > width_in and lines[-1] and not tok.isspace() and tok not in _NO_LINE_START:
            # 超长英文单词单独成行也放不下时按字符硬切
            if w > width_in:
                for ch in tok:
                    cw = text_width(ch, size_pt, bold)
                    if cur_w + cw > width_in and lines[-1]:
                        lines.append([])
                        cur_w = 0.0
                    lines[-1].append((ch, bold))
                    cur_w += cw
                continue
            lines.append([])
            cur_w = 0.0
        if tok.isspace() and not lines[-1]:
            continue  # 行首空白丢掉
        lines[-1].append((tok, bold))
        cur_w += w
    # 合并同样式的相邻片段
    merged = []
    for ln in lines:
        out: list[tuple[str, bool]] = []
        for t, b in ln:
            if out and out[-1][1] == b:
                out[-1] = (out[-1][0] + t, b)
            else:
                out.append((t, b))
        if out:
            out[-1] = (out[-1][0].rstrip(), out[-1][1])
        merged.append(out)
    return merged or [[]]


# ---------------------------------------------------------------- 内容文件
def load_content(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"{path} 不是合法 JSON：第 {e.lineno} 行第 {e.colno} 列 {e.msg}")


def load_figures(content_path: Path, content: dict) -> dict[str, dict]:
    """读取 extract 阶段生成的 figures.json，返回 {id: info}，file 字段为绝对路径。"""
    base = Path(content_path).resolve().parent
    reg = base / content.get("figures_index", "extract/figures.json")
    figs: dict[str, dict] = {}
    if reg.exists():
        for f in json.loads(reg.read_text(encoding="utf-8")):
            f = dict(f)
            f["file"] = str((reg.parent / f["file"]).resolve())
            figs[f["id"]] = f
    return figs


def resolve_figure(ref, figs: dict[str, dict], content_path: Path, crop=None) -> dict | None:
    """图引用可以是 figures.json 里的 id（如 fig3、tab2、page4），也可以是相对 content.json 的图片路径。

    ref 也可以写成 {"ref": "fig5", "crop": [x0, y0, x1, y1]}，crop 为 0–1 的相对坐标，
    用来只放大图中的一部分（细长的代码图、多子图里的某一个子图）。
    """
    if isinstance(ref, dict):
        crop = ref.get("crop", crop)
        ref = ref.get("ref")
    if not ref:
        return None
    if ref in figs:
        fig = dict(figs[ref])
    else:
        p = (Path(content_path).resolve().parent / ref)
        if not p.exists():
            return None
        fig = {"id": ref, "file": str(p), "caption": "", "label": ""}
    if crop:
        fig = _crop_figure(fig, crop, Path(content_path).resolve().parent)
    return fig


def _crop_figure(fig: dict, crop, base: Path) -> dict:
    from PIL import Image
    x0, y0, x1, y1 = [float(v) for v in crop]
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError(f"crop 必须是 0–1 之间的 [x0, y0, x1, y1]：{crop}")
    out_dir = base / "build" / "crops"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = "_".join(f"{v:.3f}" for v in (x0, y0, x1, y1))
    out = out_dir / f"{fig['id']}_{tag}.png"
    if not out.exists() or out.stat().st_mtime < Path(fig["file"]).stat().st_mtime:
        with Image.open(fig["file"]) as im:
            W, H = im.size
            im.crop((int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H))).save(out)
    return {**fig, "file": str(out), "id": f"{fig['id']}@{tag}"}


def slug_of(content: dict) -> str:
    meta = content.get("meta", {})
    s = meta.get("short_name") or meta.get("title") or "paper"
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", s).strip("_")
    return s[:60] or "paper"
