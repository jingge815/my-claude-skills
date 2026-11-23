"""用 PIL 绘制科研项目申请书的中文框架图 / 流程图，内置重叠审计，任一项失败即退出 1。

用法: .venv/bin/python scripts/draw_figure.py <spec.json> <out.png>
      写入 <out.png> 与 <out.png>.audit.txt>（逐项审计 PASS/FAIL，末尾可追加视觉自检记录）。

spec.json 两种类型（画法与字段以本 docstring 为准，详见 references/figures.md）：

1) type = "framework"  —— 研究框架图 / 总体架构图（分组容器 = 研究内容，条目 = 模块）
{
  "type": "framework",
  "title": "异构融合智能系统总体研究框架",        // 可选；仅 show_title=true 时画在顶部
  "show_title": false,                           // 默认 false：图注在文档里，图内不重复
  "layout": "row",                              // row（分组横排，≤4 组自动 2×2，"per_row" 可改）| column（5–6 组竖排）
  "bands": [ {"name": "科学问题与需求输入", "items": ["多厂商异构算力", "集群动态负载"]} ],
  "groups": [
    {"id": "g1", "title": "研究内容1  统一中间表示与符号成本", "tag": "编译期",
     "items": ["1.1 统一IR与语义契约", "1.2 两层编译与等价锚点"],
     "output": "统一IR + 符号成本系数"}          // tag 画在标题条右侧；output 画在容器底部小条
  ],
  "edges": [["g1", "g2", "成本系数"], ["g2", "g3"], ["g1", "g4", "内存预算"]],
  "footer": {"name": "验证载体", "items": ["软件栈原型", "集群调度原型"]},
  "forbidden_variants": ["系统栈", "平台"]        // 可选：出现在任何文字里即 FAIL
}
   边路由：column 下相邻分组走中轴；非相邻分组走左侧专用 lane（标签竖排）；非末组→footer 走右侧 lane。
           row 下相邻分组走组间空隙；非相邻走顶部专用 arc lane；标签横排在水平段上。
   lane/arc 只在需要时预留，预留区域无任何框，因此不会穿框。

2) type = "flow"  —— 技术路线图 / 流程图（沿用 paper-reader 的两种布局，画布宽固定 1600）
{
  "type": "flow", "title": "…", "lanes": ["内存管理路径", "执行路径"],   // 有 lanes 即泳道布局
  "nodes": [ {"id": "a", "text": "…", "level": 0, "span": true},
             {"id": "b", "text": "…", "level": 1, "lane": 0} ],      // 无 lanes 时用 level/col
  "edges": [["a", "b"], ["a", "c", "边上标签"]]
}

审计项（任一 FAIL 退出 1，PNG 仍写出供查看）：
  text-in-box      每个框内文字实际渲染宽高都在框内（留白 ≥ 8px）
  node×node        任意两个框不相交且间距 ≥ 6px
  group-containment 条目完全在容器内；容器之间、容器与 band 不相交
  label×node / label×label / label×edge  边标签不压框、不压其他标签、不压其他边
  edge-through-node 边的每一段不穿过非源非目标的框
  text-in-canvas   全部元素在画布内且留边 ≥ 8px
  font-size        节点文字 ≥ 18px、标签 ≥ 14px（逻辑像素）
  term-consistency forbidden_variants 中的词不出现

调试钩子（仅用于测试审计器）："__debug_shift": {"id": "x", "dx": 0, "dy": 0} 在布局后、审计前平移某元素。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCALE = 2                      # 2x 超采样，缩小后文字更清晰
# 画布逻辑宽固定 1400：按 16cm 版心放置时 3.09 px/pt，于是 28px ≈ 9.1pt、22px ≈ 7.1pt，
# 是正文可读的下限；再小的字号印出来看不清，再大的画布同样字号就更小。
W_LOGICAL = 1400
PAD = 40
FS_NODE, FS_LABEL, FS_TITLE, FS_HEAD, FS_TAG = 28, 22, 36, 32, 22
INNER_PAD = 12                 # 文字距框边
LINE_GAP = 8
SIDE_W = 130                   # 侧 lane 宽（非相邻边走这里，竖排标签）
LABEL_PAD = 4
C_GROUP_FILL, C_GROUP_LINE = "#F5F8FC", "#3A5F8A"
C_TITLEBAR = "#DCE8F5"
C_BAND_FILL, C_BAND_LINE = "#FFF7E6", "#C8A24A"
C_FOOT_FILL, C_FOOT_LINE = "#EFF5EC", "#5B8C5A"
C_ITEM_FILL, C_ITEM_LINE = "#FFFFFF", "#6C8EBF"
C_EDGE, C_TEXT, C_LABEL_BG = "#555555", "#1A1A1A", "#FFFFFF"
C_OUTPUT_FILL, C_OUTPUT_LINE = "#F3EEF9", "#8E6BB5"
FONT_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"


def die(msg: str):
    print(f"[draw] 错误: {msg}", file=sys.stderr)
    sys.exit(1)


@dataclass
class El:
    """一个可审计的矩形元素。坐标为逻辑像素。"""
    id: str
    rect: list                      # [x0, y0, x1, y1]
    kind: str                       # band / band_item / group / item / node / label / title / output / lane_head
    text: str = ""
    lines: list = field(default_factory=list)
    font: object = None
    fill: str = "#FFFFFF"
    outline: str = "#999999"
    parent: str | None = None
    rotated: bool = False           # 竖排标签
    pad: int = 0                    # 文字距框边（审计用）


@dataclass
class Edge:
    src: str
    dst: str
    label: str
    pts: list = field(default_factory=list)     # 折线顶点
    label_el: El | None = None
    route: str = ""                              # center / side / arc / straight


class Audit:
    def __init__(self):
        self.items: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, msg: str = ""):
        self.items.append((name, ok, msg))

    def failures(self):
        return [(n, m) for n, ok, m in self.items if not ok]

    def write(self, path: Path):
        lines = [f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {msg}" if msg else "") for name, ok, msg in self.items]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 几何工具
def inter(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def gap_lt(a, b, g):
    """两矩形间距（任一方向分离时的最近距离）小于 g。"""
    dx = max(0, max(a[0] - b[2], b[0] - a[2]))
    dy = max(0, max(a[1] - b[3], b[1] - a[3]))
    return (dx * dx + dy * dy) ** 0.5 < g


def contains(outer, inner, tol=0):
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def seg_hits_rect(p, q, r):
    """线段 p-q 与矩形 r（含边界）是否相交。"""
    x0, y0 = p
    x1, y1 = q
    if max(x0, x1) < r[0] or min(x0, x1) > r[2] or max(y0, y1) < r[1] or min(y0, y1) > r[3]:
        return False
    # 端点在矩形内
    for (x, y) in (p, q):
        if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
            return True
    # 与四条边相交
    def cross(a, b, c, d):
        def ccw(A, B, C):
            return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])
        return ccw(a, b, c) != ccw(a, b, d) and ccw(a, c, d) != ccw(b, c, d)
    corners = [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]
    for i in range(4):
        if cross(p, q, corners[i], corners[(i + 1) % 4]):
            return True
    return False


# ---------------------------------------------------------------- 字体与文字
def load_font(size: int, bold=False):
    for p, idx in ((FONT_BOLD if bold else FONT_REGULAR, 2),):
        if Path(p).exists():
            return ImageFont.truetype(p, size * SCALE, index=idx)
    try:
        import subprocess
        out = subprocess.run(["fc-match", "-f", "%{file}", "Noto Sans CJK SC"],
                             capture_output=True, text=True, check=True).stdout
        return ImageFont.truetype(out.strip(), size * SCALE)
    except Exception:
        die("找不到中文字体（Noto Sans CJK）")


def text_w(font, s: str) -> float:
    return font.getlength(s) / SCALE


def wrap(text: str, font, max_w: float) -> list[str]:
    """按逻辑像素宽折行：中文逐字、英文整词，超宽的英文长词硬切。"""
    lines, cur, cur_w = [], "", 0.0
    tokens, buf = [], ""
    for ch in text:
        if ord(ch) < 128 and not ch.isspace():
            buf += ch
        else:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
    if buf:
        tokens.append(buf)
    for tk in tokens:
        w = text_w(font, tk)
        if tk == " ":
            if cur:
                cur += " "
                cur_w += w
            continue
        if w > max_w:                                  # 单个 token 比框还宽：逐字硬切
            for ch in tk:
                cw = text_w(font, ch)
                if cur_w + cw > max_w and cur:
                    lines.append(cur)
                    cur, cur_w = ch, cw
                else:
                    cur, cur_w = cur + ch, cur_w + cw
            continue
        if cur_w + w > max_w and cur:
            lines.append(cur)
            cur, cur_w = tk, w
        else:
            cur, cur_w = cur + tk, cur_w + w
    if cur or not lines:
        lines.append(cur)
    return lines


def block(text: str, font, fs: int, inner_w: float):
    """返回 (lines, line_h, total_h, max_line_w)。"""
    lines = wrap(text, font, inner_w) if text else [""]
    line_h = fs + LINE_GAP
    return lines, line_h, len(lines) * line_h, max(text_w(font, ln) for ln in lines)


def title_lines(text: str, font, max_w: float) -> list[str]:
    """分组标题折行：放不下时优先在「研究内容N」与名称之间的空格处断开，不在词中间断。"""
    if text_w(font, text) <= max_w:
        return [text]
    parts = re.split(r"\s+", text, maxsplit=1)
    if len(parts) == 2:
        return wrap(parts[0], font, max_w) + wrap(parts[1], font, max_w)
    return wrap(text, font, max_w)


# ---------------------------------------------------------------- 主类
class Figure:
    def __init__(self, spec: dict):
        self.spec = spec
        self.type = spec.get("type", "framework")
        self.f_node = load_font(FS_NODE)
        self.f_node_b = load_font(FS_NODE, bold=True)
        self.f_label = load_font(FS_LABEL)
        self.f_title = load_font(FS_TITLE, bold=True)
        self.f_head = load_font(FS_HEAD, bold=True)
        self.f_tag = load_font(FS_TAG)
        self.els: dict[str, El] = {}
        self.edges: list[Edge] = []
        self.H = 0
        self.audit = Audit()
        self._layout()
        self._debug_shift()
        self._run_audit()

    # ------------------------------------------------------------ 元素登记
    def _add(self, id_, rect, kind, text="", font=None, fill="#FFFFFF", outline="#999999",
             parent=None, inner_pad=INNER_PAD, rotated=False) -> El:
        el = El(id_, [round(v) for v in rect], kind, text, font=font, fill=fill, outline=outline,
                parent=parent, rotated=rotated, pad=inner_pad)
        if text:
            if rotated:
                el.lines = list(text)
            else:
                el.lines = wrap(text, font, (el.rect[2] - el.rect[0]) - 2 * inner_pad)
        if id_ in self.els:
            die(f"元素 id 重复: {id_}")
        self.els[id_] = el
        return el

    def _debug_shift(self):
        dbg = self.spec.get("__debug_shift")
        if not dbg:
            return
        el = self.els.get(dbg["id"])
        if el:
            dx, dy = dbg.get("dx", 0), dbg.get("dy", 0)
            el.rect = [el.rect[0] + dx, el.rect[1] + dy, el.rect[2] + dx, el.rect[3] + dy]

    # ------------------------------------------------------------ framework 布局
    def _layout(self):
        self.W = W_LOGICAL
        if self.type == "framework":
            self._layout_framework()
        elif self.type == "flow":
            self._layout_flow()
        else:
            die(f"未知 type: {self.type}")

    def _strip(self, key, y, name_fill, name_line, item_fill, item_line, kind, extra_top=0):
        """band / footer：名称在左上，items 横排。返回 (新 y, 高度)。"""
        data = self.spec.get(key)
        if not data:
            return y, 0
        items = data.get("items", [])
        x0, x1 = PAD, W_LOGICAL - PAD
        head_h = FS_HEAD + 16
        n = max(1, len(items))
        gap = 16
        iw = (x1 - x0 - 2 * INNER_PAD - (n - 1) * gap) // n      # 与绘制宽度严格一致
        tmp = [block(t, self.f_node, FS_NODE, iw - 2 * INNER_PAD) for t in items] if items else []
        ih = max([b[2] for b in tmp], default=0) + 2 * INNER_PAD
        y_top = y + extra_top
        self._add(f"{key}", [x0, y_top, x1, y_top + head_h + ih + 10], kind, fill=name_fill, outline=name_line)
        nw = text_w(self.f_head, data["name"])
        self._add(f"{key}_name", [x0 + INNER_PAD, y_top + 4, x0 + INNER_PAD + nw + 4, y_top + 4 + FS_HEAD + LINE_GAP],
                  "head", data["name"], font=self.f_head, fill=name_fill, outline=name_fill, parent=f"{key}", inner_pad=0)
        iy = y_top + head_h
        x = x0 + INNER_PAD
        for i, t in enumerate(items):
            self._add(f"{key}_item{i}", [x, iy, x + iw, iy + ih], "band_item", t, font=self.f_node,
                      fill=item_fill, outline=item_line, parent=f"{key}")
            x += iw + gap
        return y_top + head_h + ih + 10, head_h + ih + 10

    def _layout_framework(self):
        sp = self.spec
        groups = sp.get("groups", [])
        if not 0 < len(groups) <= 6:
            die("framework 需要 1–6 个 groups")
        layout = sp.get("layout", "column")
        edges = sp.get("edges", [])
        gids = [g["id"] for g in groups]
        idx = {g: i for i, g in enumerate(gids)}
        has_footer = bool(sp.get("footer"))
        # 判断是否需要侧 lane
        side_needed = any(idx[e[0]] < idx[e[1]] - 1 for e in edges if e[0] in idx and e[1] in idx)
        right_needed = has_footer and any(e[1] == "footer" and idx.get(e[0], -1) < len(groups) - 1 for e in edges)
        side_w = SIDE_W if side_needed else 0
        right_w = SIDE_W if right_needed else 0

        y = PAD
        if sp.get("show_title") and sp.get("title"):
            tw = text_w(self.f_title, sp["title"])
            self._add("title", [(W_LOGICAL - tw) / 2 - 8, y, (W_LOGICAL + tw) / 2 + 8, y + FS_TITLE + 12], "title",
                      sp["title"], font=self.f_title, fill="#FFFFFF", outline="#FFFFFF", inner_pad=0)
            y += FS_TITLE + 28
        y, _ = self._strip("bands", y, C_BAND_FILL, C_BAND_LINE, "#FFFFFF", C_BAND_LINE, "band")
        if y > PAD:
            y += 24

        if layout == "column":
            self._layout_column(y, side_w, right_w)
        else:
            self._layout_row(y, side_w, right_w)
        self._route_framework()

    def _group_box(self, g, x0, y, gw, bar_h, ih, out_h, title_lines):
        """画一个分组容器（标题条 + 条目 + 产出条），返回容器高度。column/row 共用。"""
        gid = g["id"]
        tag = g.get("tag", "")
        items = g.get("items", [])
        x1 = x0 + gw
        gh = bar_h + INNER_PAD + len(items) * ih + max(0, len(items) - 1) * 10 + INNER_PAD
        if out_h:
            gh += INNER_PAD + out_h
        self._add(gid, [x0, y, x1, y + gh], "group", fill=C_GROUP_FILL, outline=C_GROUP_LINE)
        self._add(f"{gid}_bar", [x0, y, x1, y + bar_h], "bar", fill=C_TITLEBAR, outline=C_GROUP_LINE, parent=gid)
        tl = title_lines
        tlh = len(tl) * (FS_HEAD + LINE_GAP)
        tw_max = max(text_w(self.f_head, ln) for ln in tl)
        self._add(f"{gid}_title", [x0 + INNER_PAD, y + (bar_h - tlh) // 2, x0 + INNER_PAD + tw_max + 4,
                                   y + (bar_h - tlh) // 2 + tlh], "head", font=self.f_head, fill=C_TITLEBAR,
                  outline=C_TITLEBAR, parent=gid, inner_pad=0)
        self.els[f"{gid}_title"].lines = tl
        if tag:
            tgw = text_w(self.f_tag, tag)
            self._add(f"{gid}_tag", [x1 - INNER_PAD - tgw - 16, y + (bar_h - FS_TAG - LINE_GAP) // 2 - 4, x1 - INNER_PAD,
                                     y + (bar_h - FS_TAG - LINE_GAP) // 2 + FS_TAG + LINE_GAP + 4], "head", tag,
                      font=self.f_tag, fill="#FFFFFF", outline=C_GROUP_LINE, parent=gid, inner_pad=4)
        iy = y + bar_h + INNER_PAD
        for k, t in enumerate(items):
            self._add(f"{gid}_item{k}", [x0 + INNER_PAD, iy, x1 - INNER_PAD, iy + ih], "item", t, font=self.f_node,
                      fill=C_ITEM_FILL, outline=C_ITEM_LINE, parent=gid)
            iy += ih + 10
        if out_h:
            oy = y + gh - INNER_PAD - out_h
            self._add(f"{gid}_output", [x0 + INNER_PAD, oy, x1 - INNER_PAD, oy + out_h], "output",
                      f"产出：{g['output']}", font=self.f_tag, fill=C_OUTPUT_FILL, outline=C_OUTPUT_LINE, parent=gid,
                      inner_pad=LABEL_PAD)
        return gh

    def _layout_column(self, y, side_w, right_w):
        groups = self.spec["groups"]
        gx0 = PAD + side_w
        gx1 = W_LOGICAL - PAD - right_w
        GAP_GROUP = 72
        inner_w = (gx1 - gx0) - 2 * INNER_PAD
        for g in groups:
            items = g.get("items", [])
            tmp = [block(t, self.f_node, FS_NODE, inner_w - 2 * INNER_PAD) for t in items]
            ih = max([b[2] for b in tmp], default=0) + 2 * INNER_PAD
            room = inner_w - 2 * INNER_PAD - (text_w(self.f_tag, g["tag"]) + 32 if g.get("tag") else 0)
            tl = title_lines(g.get("title", g["id"]), self.f_head, room)
            bar_h = max(FS_HEAD + 20, len(tl) * (FS_HEAD + LINE_GAP) + 12)
            out_h = FS_TAG + LINE_GAP + 2 * LABEL_PAD if g.get("output") else 0
            gh = self._group_box(g, gx0, y, gx1 - gx0, bar_h, ih, out_h, tl)
            self.els[g["id"]].rect[1] = y
            y += gh + GAP_GROUP
        y -= GAP_GROUP
        self.y_after_groups = y
        y2, _ = self._strip("footer", y + 60, C_FOOT_FILL, C_FOOT_LINE, "#FFFFFF", C_FOOT_LINE, "footer")
        self.H = int(max(y, y2) + PAD)

    def _layout_row(self, y, side_w, right_w):
        """分组横排，超过 per_row 自动换行（≤3 组一行，4 组两行，5–6 组三列两行）。"""
        sp = self.spec
        groups = sp["groups"]
        edges = sp.get("edges", [])
        gids = [g["id"] for g in groups]
        idx = {g: i for i, g in enumerate(gids)}
        n = len(groups)
        per_row = int(sp.get("per_row") or (n if n <= 3 else 2 if n == 4 else 3))
        self.per_row = per_row
        self.row_of = {g: i // per_row for i, g in enumerate(gids)}
        rows = (n + per_row - 1) // per_row
        # 侧 lane：只在「非末行分组 → footer」时需要；按该分组所在列决定左/右
        side_w = right_w = 0
        for e in edges:
            if e[1] == "footer" and e[0] in idx and self.row_of[e[0]] < rows - 1:
                c = idx[e[0]] % per_row
                if c <= (per_row - 1) / 2:
                    side_w = SIDE_W
                if c == per_row - 1 or c > (per_row - 1) / 2:
                    right_w = SIDE_W
        # arc lane：只在「第 0 行内非相邻」时需要
        arc_needed = any(idx[e[0]] < idx[e[1]] - 1 and self.row_of[e[0]] == 0 and self.row_of[e[1]] == 0
                         for e in edges if e[0] in idx and e[1] in idx)
        gx0 = PAD + side_w
        gx1 = W_LOGICAL - PAD - right_w
        # 组间空隙要容得下同行相邻边的标签；行间隙要容得下多条水平段与标签
        GAP_X, GAP_Y = 84, 140
        for e in edges:
            if len(e) > 2 and e[2] and e[0] in idx and e[1] in idx and idx[e[1]] == idx[e[0]] + 1 \
                    and self.row_of[e[0]] == self.row_of[e[1]]:
                GAP_X = max(GAP_X, int(text_w(self.f_label, e[2])) + 2 * INNER_PAD + 8)
        gw = (gx1 - gx0 - (min(n, per_row) - 1) * GAP_X) // min(n, per_row)
        inner_w = gw - 2 * INNER_PAD
        titles = []
        for g in groups:
            room = inner_w - 2 * INNER_PAD - (text_w(self.f_tag, g["tag"]) + 32 if g.get("tag") else 0)
            titles.append(title_lines(g.get("title", g["id"]), self.f_head, room))
        bar_h = max(FS_HEAD + 20, max(len(t) for t in titles) * (FS_HEAD + LINE_GAP) + 12)
        heights = []
        for g in groups:
            tmp = [block(t, self.f_node, FS_NODE, inner_w - 2 * INNER_PAD) for t in g.get("items", [])]
            ih = max([b[2] for b in tmp], default=0) + 2 * INNER_PAD
            out_h = FS_TAG + LINE_GAP + 2 * LABEL_PAD if g.get("output") else 0
            heights.append(bar_h + INNER_PAD + len(g["items"]) * ih + max(0, len(g["items"]) - 1) * 10 + INNER_PAD
                           + (INNER_PAD + out_h if out_h else 0))
        gh = max(heights)
        self.arc_y = y
        y0 = y + (72 if arc_needed else 0)
        self.row_top = [y0 + r * (gh + GAP_Y) for r in range(rows)]
        for i, g in enumerate(groups):
            r, c = i // per_row, i % per_row
            in_row = min(n - r * per_row, per_row)
            total = in_row * gw + (in_row - 1) * GAP_X
            x = gx0 + (gx1 - gx0 - total) // 2 + c * (gw + GAP_X)
            self._group_box(g, x, y0 + r * (gh + GAP_Y), gw, bar_h, heights[i] and
                            (max([block(t, self.f_node, FS_NODE, inner_w - 2 * INNER_PAD)[2] for t in g["items"]]) + 2 * INNER_PAD),
                            FS_TAG + LINE_GAP + 2 * LABEL_PAD if g.get("output") else 0, titles[i])
        last_bottom = y0 + (rows - 1) * (gh + GAP_Y) + gh
        self.y_after_groups = last_bottom
        y2, _ = self._strip("footer", last_bottom + 60, C_FOOT_FILL, C_FOOT_LINE, "#FFFFFF", C_FOOT_LINE, "footer")
        self.H = int(max(last_bottom, y2) + PAD)

    def _center(self, el: El, side: str):
        x0, y0, x1, y1 = el.rect
        return {"top": ((x0 + x1) / 2, y0), "bottom": ((x0 + x1) / 2, y1),
                "left": (x0, (y0 + y1) / 2), "right": (x1, (y0 + y1) / 2)}[side]

    def _route_framework(self):
        sp = self.spec
        groups = sp["groups"]
        gids = [g["id"] for g in groups]
        idx = {g: i for i, g in enumerate(gids)}
        layout = sp.get("layout", "column")
        n = len(groups)
        rows = getattr(self, "row_of", None) and (max(self.row_of.values()) + 1)
        side_k = right_k = arc_k = gap_k = 0
        LH = FS_LABEL + LINE_GAP + 2 * LABEL_PAD          # 标签框高
        for e in sp.get("edges", []):
            src, dst = e[0], e[1]
            label = e[2] if len(e) > 2 and e[2] else ""
            if src not in self.els or dst not in self.els:
                die(f"边 {src}→{dst} 引用了不存在的元素")
            E = Edge(src, dst, label)
            s_in, d_in = src in idx, dst in idx
            C = self._center
            if layout == "column":
                if s_in and d_in and idx[dst] == idx[src] + 1:
                    a, b = C(self.els[src], "bottom"), C(self.els[dst], "top")
                    E.pts, E.route = [a, (a[0], b[1])], "center"
                elif s_in and d_in and idx[dst] > idx[src] + 1:
                    lm = 30 + 40 * side_k
                    side_k += 1
                    a, b = C(self.els[src], "left"), C(self.els[dst], "left")
                    E.pts, E.route = [a, (lm, a[1]), (lm, b[1]), b], "side"
                elif dst == "footer":
                    a = C(self.els[src], "bottom" if idx.get(src, -1) == n - 1 else "right")
                    if idx.get(src, -1) == n - 1:
                        E.pts, E.route = [a, C(self.els[dst], "top")], "center"
                    else:
                        rm = W_LOGICAL - 30 - 40 * right_k
                        right_k += 1
                        b = C(self.els[dst], "right")
                        E.pts, E.route = [a, (rm, a[1]), (rm, b[1]), b], "side"
                else:
                    E.pts, E.route = [C(self.els[src], "bottom"), C(self.els[dst], "top")], "straight"
            else:
                rs, rd = self.row_of.get(src, 0), self.row_of.get(dst, 0)
                cs, cd = idx.get(src, 0) % self.per_row, idx.get(dst, 0) % self.per_row
                last_row = rows - 1
                if s_in and d_in and idx[dst] == idx[src] + 1 and rs == rd:
                    E.pts, E.route = [C(self.els[src], "right"), C(self.els[dst], "left")], "row"
                elif s_in and d_in and idx[dst] == idx[src] + 1:
                    # 跨行相邻：下→行间隙→上
                    a, b = C(self.els[src], "bottom"), C(self.els[dst], "top")
                    gy = self.row_top[rd] - 64
                    E.pts, E.route = [a, (a[0], gy), (b[0], gy), b], "elbow"
                elif s_in and d_in and abs(idx[dst] - idx[src]) > 1 and rs == rd and rs == 0:
                    ay = self.arc_y + 14 + 20 * arc_k
                    arc_k += 1
                    a, b = C(self.els[src], "top"), C(self.els[dst], "top")
                    E.pts, E.route = [a, (a[0], ay), (b[0], ay), b], "arc"
                elif s_in and d_in and rd > rs and abs(idx[dst] - idx[src]) > 1:
                    # 跨行向前跳过：下→目标行上方的间隙→上；与相邻边的水平段错开
                    a, b = C(self.els[src], "bottom"), C(self.els[dst], "top")
                    gy = self.row_top[rd] - 64 - 44 * (gap_k + 1)
                    gap_k += 1
                    E.pts, E.route = [a, (a[0], gy), (b[0], gy), b], "elbow"
                elif s_in and d_in and rd < rs:
                    # 跨行向后：上→源行上方的间隙→下
                    a, b = C(self.els[src], "top"), C(self.els[dst], "bottom")
                    gy = self.row_top[rs] - 64 - 44 * (gap_k + 1)
                    gap_k += 1
                    E.pts, E.route = [a, (a[0], gy), (b[0], gy), b], "elbow"
                elif s_in and d_in:
                    # 同行非相邻且不在第 0 行：上→本行上方的间隙→下
                    a, b = C(self.els[src], "top"), C(self.els[dst], "top")
                    gy = self.row_top[rs] - 64 - 44 * (gap_k + 1)
                    gap_k += 1
                    E.pts, E.route = [a, (a[0], gy), (b[0], gy), b], "elbow"
                elif dst == "footer":
                    if rs == last_row:
                        E.pts, E.route = [C(self.els[src], "bottom"), C(self.els[dst], "top")], "center"
                    else:
                        # 非末行 → footer：按所在列选侧 lane；中间列先下行到本行下方间隙再进 lane
                        to_left = cs <= (self.per_row - 1) / 2
                        if cs == 0:
                            lm, side_k = 30 + 40 * side_k, side_k + 1
                            a, b = C(self.els[src], "left"), C(self.els[dst], "left")
                            E.pts, E.route = [a, (lm, a[1]), (lm, b[1]), b], "side"
                        elif cs == self.per_row - 1:
                            rm, right_k = W_LOGICAL - 30 - 40 * right_k, right_k + 1
                            a, b = C(self.els[src], "right"), C(self.els[dst], "right")
                            E.pts, E.route = [a, (rm, a[1]), (rm, b[1]), b], "side"
                        else:
                            if to_left:
                                lm, side_k = 30 + 40 * side_k, side_k + 1
                            else:
                                lm, right_k = W_LOGICAL - 30 - 40 * right_k, right_k + 1
                            a = C(self.els[src], "bottom")
                            gy = self.row_top[rs + 1] - 24
                            b = C(self.els[dst], "left" if to_left else "right")
                            E.pts, E.route = [a, (a[0], gy), (lm, gy), (lm, b[1]), b], "side"
                else:
                    E.pts, E.route = [C(self.els[src], "bottom"), C(self.els[dst], "top")], "straight"
            self.edges.append(E)
        self._place_edge_labels()

    def _label_rect_h(self, xc, yc, text):
        tw = text_w(self.f_label, text)
        return [xc - tw / 2 - LABEL_PAD, yc - (FS_LABEL + LINE_GAP) / 2 - LABEL_PAD,
                xc + tw / 2 + LABEL_PAD, yc + (FS_LABEL + LINE_GAP) / 2 + LABEL_PAD]

    def _place_edge_labels(self):
        LH = FS_LABEL + LINE_GAP + 2 * LABEL_PAD
        n = 0
        for E in self.edges:
            if not E.label:
                continue
            n += 1
            lid = f"label{n}"
            r = None
            if E.route == "center":
                # 竖直段右侧，垂直居中于段中点
                (x0, y0), (x1, y1) = E.pts[0], E.pts[-1]
                tw = text_w(self.f_label, E.label)
                ym = (y0 + y1) / 2
                r = [x0 + 10, ym - LH / 2, x0 + 10 + tw + 2 * LABEL_PAD, ym + LH / 2]
            elif E.route == "side":
                xm = E.pts[1][0]
                ya, yb = E.pts[1][1], E.pts[2][1]
                ym = (ya + yb) / 2
                th = len(E.label) * (FS_LABEL + 2)
                r = [xm - FS_LABEL / 2 - 6, ym - th / 2, xm + FS_LABEL / 2 + 6, ym + th / 2]
                E.label_el = self._add(lid, r, "label", E.label, font=self.f_label, fill=C_LABEL_BG, outline=C_LABEL_BG,
                                       inner_pad=0, rotated=True)
                continue
            elif E.route == "row":
                (xa, ya), (xb, yb) = E.pts[0], E.pts[-1]
                xm = (xa + xb) / 2
                r = self._label_rect_h(xm, ya - LH / 2 - 2, E.label)
            elif E.route == "arc":
                (xa, ya), (xb, yb) = E.pts[1], E.pts[2]
                r = self._label_rect_h((xa + xb) / 2, ya, E.label)
            elif E.route == "elbow":
                (xa, ya), (xb, yb) = E.pts[1], E.pts[2]
                r = self._label_rect_h((xa + xb) / 2, ya, E.label)
            else:
                (xa, ya), (xb, yb) = E.pts[0], E.pts[-1]
                if abs(xa - xb) < 1:
                    r = self._label_rect_h(xa + text_w(self.f_label, E.label) / 2 + LABEL_PAD + 10, (ya + yb) / 2, E.label)
                else:
                    r = self._label_rect_h((xa + xb) / 2, ya - LH / 2 - 2, E.label)
            E.label_el = self._add(lid, r, "label", E.label, font=self.f_label, fill=C_LABEL_BG, outline=C_LABEL_BG,
                                   inner_pad=LABEL_PAD)

    # ------------------------------------------------------------ flow 布局
    def _layout_flow(self):
        sp = self.spec
        nodes = sp.get("nodes", [])
        if not nodes:
            die("flow 需要 nodes")
        self.lanes = sp.get("lanes")
        lane_mode = self.lanes is not None
        ids = {n["id"] for n in nodes}
        for n in nodes:
            if lane_mode and "span" not in n and "lane" not in n:
                die(f"节点 {n['id']} 缺少 lane 或 span 字段")
            if not lane_mode and "level" not in n:
                die(f"节点 {n['id']} 缺少 level 字段")
        GAP_X, GAP_Y = 46, 64          # 行间隙要容得下跨层边的标签（标签框高 38）
        gap_x = GAP_X
        by_id = {n["id"]: n for n in nodes}
        for e in sp.get("edges", []):
            if len(e) > 2 and e[2] and e[0] in by_id and e[1] in by_id:
                if by_id[e[0]].get("level") == by_id[e[1]].get("level"):
                    gap_x = max(gap_x, int(text_w(self.f_label, e[2])) + 26)

        def node_box(nid, x, y, w, text):
            lines, lh, th, _ = block(text, self.f_node, FS_NODE, w - 2 * INNER_PAD)
            h = max(46, th + 2 * INNER_PAD)
            el = self._add(nid, [x, y, x + w, y + h], "node", text, font=self.f_node,
                           fill="#F5F8FC" if not by_id[nid].get("span") else "#EFF5EC",
                           outline="#3A5F8A" if not by_id[nid].get("plain") else "#999999")
            return el

        lane_heads = []
        if lane_mode:
            n_lane = max(1, len(self.lanes))
            lane_w = (W_LOGICAL - 2 * PAD - (n_lane - 1) * gap_x) // n_lane
            span_w = n_lane * lane_w + (n_lane - 1) * gap_x
            head_h = 40
            y = PAD + head_h + 14
            if sp.get("show_title") and sp.get("title"):
                y += 44
            for i, name in enumerate(self.lanes):
                x0 = PAD + i * (lane_w + gap_x)
                lane_heads.append(self._add(f"lane{i}", [x0, y - head_h - 14, x0 + lane_w, y - 14], "lane_head", name,
                                            font=self.f_tag, fill="#E8F0F8", outline="#3A5F8A", inner_pad=0))
            levels = sorted({n["level"] for n in nodes})
            ys = {}
            for lv in levels:
                hs = []
                for n in nodes:
                    if n["level"] != lv:
                        continue
                    w = span_w if n.get("span") else lane_w
                    lines, lh, th, _ = block(n["text"], self.f_node, FS_NODE, w - 2 * INNER_PAD)
                    hs.append(max(46, th + 2 * INNER_PAD))
                ys[lv] = y
                y += max(hs) + GAP_Y
            for n in nodes:
                w = span_w if n.get("span") else lane_w
                x0 = PAD if n.get("span") else PAD + n["lane"] * (lane_w + gap_x)
                node_box(n["id"], x0, ys[n["level"]], w, n["text"])
            W = span_w + 2 * PAD
            H = y - GAP_Y + PAD
        else:
            levels = {}
            for n in nodes:
                levels.setdefault(n["level"], []).append(n)
            for lv in levels:
                levels[lv].sort(key=lambda n: n.get("col", 0))
            max_in_row = max(len(v) for v in levels.values())
            box_w = 420 if max_in_row == 1 else min(560, (W_LOGICAL - 2 * PAD - (max_in_row - 1) * gap_x) // max_in_row)
            y = PAD + (44 if sp.get("show_title") and sp.get("title") else 0)
            for lv in sorted(levels):
                ns = levels[lv]
                total = len(ns) * box_w + (len(ns) - 1) * gap_x
                x = (W_LOGICAL - total) / 2
                hs = [max(46, block(n["text"], self.f_node, FS_NODE, box_w - 2 * INNER_PAD)[2] + 2 * INNER_PAD) for n in ns]
                h = max(hs)
                for n in ns:
                    node_box(n["id"], x, y, box_w, n["text"])
                    x += box_w + gap_x
                y += h + GAP_Y
            H = y - GAP_Y + PAD
        self.W, self.H = W_LOGICAL, int(H)
        self.lane_heads = lane_heads

        # 边
        n_lab = 0
        for e in sp.get("edges", []):
            if e[0] not in self.els or e[1] not in self.els:
                die(f"边 {e[0]}→{e[1]} 引用了不存在的节点")
            E = Edge(e[0], e[1], e[2] if len(e) > 2 and e[2] else "")
            a, b = self.els[e[0]], self.els[e[1]]
            same_row = a.rect[1] == b.rect[1]
            if same_row and a.rect != b.rect:
                ax, bx = (a.rect[2], b.rect[0]) if a.rect[0] <= b.rect[0] else (a.rect[0], b.rect[2])
                ay = by = (a.rect[1] + a.rect[3]) / 2
                E.pts = [(ax, ay), (bx, by)]
                E.route = "straight"
                if E.label:
                    n_lab += 1
                    tw = text_w(self.f_label, E.label)
                    LH = FS_LABEL + LINE_GAP + 2 * LABEL_PAD
                    lx = (ax + bx) / 2 - tw / 2
                    ly = ay - LH - 6
                    E.label_el = self._add(f"label{n_lab}", [lx - LABEL_PAD, ly, lx + tw + LABEL_PAD, ly + LH], "label",
                                           E.label, font=self.f_label, fill=C_LABEL_BG, outline=C_LABEL_BG,
                                           inner_pad=LABEL_PAD)
            else:
                ax, ay = (a.rect[0] + a.rect[2]) / 2, a.rect[3]
                bx, by = (b.rect[0] + b.rect[2]) / 2, b.rect[1]
                if by < ay:                                      # 目标在上方：从源顶到目标底
                    ax, ay = (a.rect[0] + a.rect[2]) / 2, a.rect[1]
                    bx, by = (b.rect[0] + b.rect[2]) / 2, b.rect[3]
                midy = ay + (by - ay) * 0.5
                E.pts = [(ax, ay), (ax, midy), (bx, midy), (bx, by)]
                E.route = "elbow"
                if E.label:
                    n_lab += 1
                    tw = text_w(self.f_label, E.label)
                    LH = FS_LABEL + LINE_GAP + 2 * LABEL_PAD
                    lx = (ax + bx) / 2 - tw / 2
                    E.label_el = self._add(f"label{n_lab}", [lx - LABEL_PAD, midy - LH / 2, lx + tw + LABEL_PAD,
                                                             midy + LH / 2], "label",
                                           E.label, font=self.f_label, fill=C_LABEL_BG, outline=C_LABEL_BG,
                                           inner_pad=LABEL_PAD)
            self.edges.append(E)
        if sp.get("show_title") and sp.get("title"):
            tw = text_w(self.f_title, sp["title"])
            y_t = PAD - 10
            self._add("title", [(W_LOGICAL - tw) / 2 - 8, y_t, (W_LOGICAL + tw) / 2 + 8, y_t + FS_TITLE + 12], "title",
                      sp["title"], font=self.f_title, fill="#FFFFFF", outline="#FFFFFF", inner_pad=0)

    # ------------------------------------------------------------ 审计
    LAYER = {"group": "c", "band": "c", "footer": "c",
             "item": "x", "band_item": "x", "output": "x", "node": "x",
             "label": "l", "bar": "d", "head": "d", "lane_head": "d", "title": "d"}

    def _run_audit(self):
        A = self.audit
        W, H = self.W, self.H
        els = list(self.els.values())

        # 1 text-in-box
        bad = []
        for el in els:
            if not el.lines or el.font is None:
                continue
            fs = el.font.size / SCALE
            if el.rotated:
                th = len(el.lines) * (fs + 2)
                mw = max(text_w(el.font, c) for c in el.lines)
            else:
                th = len(el.lines) * (fs + LINE_GAP)
                mw = max(text_w(el.font, ln) for ln in el.lines)
            pad = getattr(el, "pad", INNER_PAD) if el.kind in ("item", "band_item", "node", "output", "label") else 0
            x0, y0, x1, y1 = el.rect
            if mw + 2 * pad > (x1 - x0) + 1 or th + 2 * pad > (y1 - y0) + 1:
                bad.append(f"{el.id}（需 {mw + 2 * pad:.0f}×{th + 2 * pad:.0f}，框 {x1 - x0:.0f}×{y1 - y0:.0f}）")
        A.add("text-in-box", not bad, "；".join(bad))

        # 2 node×node + 8 min-gap + 3 containment
        bad, badc = [], []
        for i in range(len(els)):
            for j in range(i + 1, len(els)):
                a, b = els[i], els[j]
                la, lb = self.LAYER.get(a.kind), self.LAYER.get(b.kind)
                if la == "d" and lb == "d":
                    # 标题/角标本来就画在标题条上，bar 与同容器的 head 不算相交
                    if "bar" in (a.kind, b.kind):
                        continue
                    if a.parent and a.parent == b.parent and inter(a.rect, b.rect):
                        bad.append(f"{a.id} 与 {b.id} 相交")
                    continue
                if la == "d" or lb == "d":
                    d, o = (a, b) if la == "d" else (b, a)
                    if o.kind == "label" and (inter(d.rect, o.rect) or gap_lt(d.rect, o.rect, 6)):
                        bad.append(f"标签 {o.id} 与 {d.id} 相交或间距不足")
                    continue
                if (a.parent and a.parent == b.id) or (b.parent and b.parent == a.id):
                    continue
                if inter(a.rect, b.rect):
                    bad.append(f"{a.id} 与 {b.id} 相交")
                elif gap_lt(a.rect, b.rect, 6):
                    bad.append(f"{a.id} 与 {b.id} 间距不足 6px")
        for el in els:
            if el.parent and el.parent in self.els and not contains(self.els[el.parent].rect, el.rect, 0):
                badc.append(f"{el.id} 超出容器 {el.parent}")
        A.add("node×node", not bad, "；".join(bad))
        A.add("min-gap", not [m for m in bad if "间距" in m], "")
        A.add("group-containment", not badc, "；".join(badc))

        # 4 label×edge（非自身边）
        bad = []
        for E in self.edges:
            L = E.label_el
            if L is None:
                continue
            for F in self.edges:
                if F is E:
                    continue
                for (p, q) in zip(F.pts, F.pts[1:]):
                    if seg_hits_rect(p, q, L.rect):
                        bad.append(f"标签「{L.text}」压在边 {F.src}→{F.dst} 上")
        A.add("label×edge", not bad, "；".join(bad))

        # 6 edge-through-node（只豁免源与目标本身；它们内部的条目仍有 INNER_PAD 余量，正常布局不会被端点碰到）
        bad = []
        for E in self.edges:
            skip = {E.src, E.dst}
            for (p, q) in zip(E.pts, E.pts[1:]):
                for el in els:
                    if el.id in skip or el.kind in ("label", "bar", "head", "lane_head", "title"):
                        continue
                    if seg_hits_rect(p, q, el.rect):
                        bad.append(f"边 {E.src}→{E.dst} 穿过 {el.id}")
        A.add("edge-through-node", not bad, "；".join(bad))

        # 5 text-in-canvas
        bad = [el.id for el in els if not contains([8, 8, W - 8, H - 8], el.rect, 0)]
        for E in self.edges:
            for (x, y) in E.pts:
                if not (8 <= x <= W - 8 and 8 <= y <= H - 8):
                    bad.append(f"边 {E.src}→{E.dst} 出界")
        A.add("text-in-canvas", not bad, "；".join(bad))

        # 7 font-size
        A.add("font-size", FS_NODE >= 18 and FS_LABEL >= 14 and FS_TAG >= 14, "")
        ar = W / H
        if not 0.9 <= ar <= 2.2:
            print(f"[draw] 警告: 长宽比 {ar:.2f} 超出 [0.9, 2.2]，图过长或过扁，建议减少分组或改布局", file=sys.stderr)

        # 9 term-consistency
        bad = []
        for v in self.spec.get("forbidden_variants", []):
            for el in els:
                if v in el.text:
                    bad.append(f"「{v}」出现在 {el.id}")
        A.add("term-consistency", not bad, "；".join(bad))

    # ------------------------------------------------------------ 渲染
    def render(self, out: Path):
        W, H = self.W, self.H
        img = Image.new("RGB", (W * SCALE, H * SCALE), "white")
        d = ImageDraw.Draw(img)

        def R(r):
            return [v * SCALE for v in r]

        def draw_text(el: El):
            if not el.lines or el.font is None:
                return
            x0, y0, x1, y1 = [v * SCALE for v in el.rect]
            fs = el.font.size
            if el.rotated:
                lh = fs + 2 * SCALE
                total = lh * len(el.lines)
                ty = (y0 + y1) / 2 - total / 2
                for c in el.lines:
                    tw = d.textlength(c, font=el.font)
                    d.text(((x0 + x1) / 2 - tw / 2, ty), c, font=el.font, fill=C_TEXT)
                    ty += lh
                return
            lh = fs + LINE_GAP * SCALE
            total = lh * len(el.lines)
            pad = getattr(el, "pad", INNER_PAD) * SCALE if el.kind in ("item", "band_item", "node", "output", "label") else 0
            ty = (y0 + y1) / 2 - total / 2
            align_left = el.kind in ("head",) and el.parent in self.els and self.els[el.parent].kind in ("band", "footer")
            for ln in el.lines:
                tw = d.textlength(ln, font=el.font)
                tx = x0 + pad if align_left else ((x0 + x1) / 2 - tw / 2)
                d.text((tx, ty + fs * 0.1), ln, font=el.font, fill=C_TEXT)
                ty += lh

        # 边（先画线，再白底标签，最后画框覆盖线端）
        for E in self.edges:
            pts = [(x * SCALE, y * SCALE) for (x, y) in E.pts]
            if len(pts) >= 2:
                d.line(pts, fill=C_EDGE, width=2 * SCALE, joint="curve")
            if E.label_el is not None:
                d.rectangle(R(E.label_el.rect), fill=C_LABEL_BG)
        # 箭头
        for E in self.edges:
            (px, py), (qx, qy) = E.pts[-2], E.pts[-1]
            ah = 7
            dx, dy = qx - px, qy - py
            if abs(dy) >= abs(dx):
                sgn = 1 if dy > 0 else -1
                tri = [(qx, qy), (qx - ah, qy - sgn * ah * 1.6), (qx + ah, qy - sgn * ah * 1.6)]
            else:
                sgn = 1 if dx > 0 else -1
                tri = [(qx, qy), (qx - sgn * ah * 1.6, qy - ah), (qx - sgn * ah * 1.6, qy + ah)]
            d.polygon([(x * SCALE, y * SCALE) for x, y in tri], fill=C_EDGE)

        # 元素：容器与框
        order = {"band": 0, "footer": 0, "group": 0, "lane_head": 0, "bar": 1, "output": 2, "item": 2, "band_item": 2,
                 "node": 2, "head": 3, "label": 4, "title": 3}
        for el in sorted(self.els.values(), key=lambda e: order.get(e.kind, 3)):
            r = R(el.rect)
            if el.kind in ("band", "footer", "group", "lane_head"):
                d.rounded_rectangle(r, radius=8 * SCALE, fill=el.fill, outline=el.outline, width=SCALE + 1)
            elif el.kind == "bar":
                d.rounded_rectangle([r[0] + (SCALE + 1), r[1] + (SCALE + 1), r[2] - (SCALE + 1), r[3]],
                                    radius=7 * SCALE, fill=el.fill, outline=el.outline, width=SCALE + 1)
            elif el.kind in ("item", "band_item", "output", "node"):
                d.rounded_rectangle(r, radius=6 * SCALE, fill=el.fill, outline=el.outline, width=SCALE + 1)
            elif el.kind == "label":
                d.rectangle(r, fill=el.fill)
            draw_text(el)

        img.resize((W, H), Image.LANCZOS).save(out)

    def write_audit(self, path: Path):
        self.audit.write(path)
        for name, msg in self.audit.failures():
            print(f"[draw] 错误: 审计失败 {name}" + (f" — {msg}" if msg else ""), file=sys.stderr)
        n_ok = sum(1 for _, ok, _ in self.audit.items if ok)
        n = len(self.audit.items)
        if self.audit.failures():
            print(f"[draw] 完成(未通过): {path} ({self.W}x{self.H})，审计 {n_ok}/{n} 通过，见 {path.name}.audit.txt",
                  file=sys.stderr)
            sys.exit(1)
        print(f"[draw] 完成: {path} ({self.W}x{self.H}, 审计 {n} 项全部通过)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("out")
    args = ap.parse_args()
    sp = Path(args.spec)
    if not sp.exists():
        die(f"找不到 spec: {sp}")
    spec = json.loads(sp.read_text(encoding="utf-8"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig = Figure(spec)
    fig.W = W_LOGICAL
    fig.render(out)
    fig.write_audit(Path(str(out) + ".audit.txt"))


if __name__ == "__main__":
    main()
