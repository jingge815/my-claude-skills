"""用 PIL 绘制中文流程图/框架图（论文中没有现成图时自绘用）。

用法: .venv/bin/python scripts/draw_flow.py <spec.json> <输出.png>

spec.json 支持两种布局：

1) 泳道布局（推荐，体现并行路径/两路）——定义 "lanes" 即启用：
{
  "title": "PATTON 运行时两路径工作流程",
  "lanes": ["内存管理路径", "执行路径"],
  "nodes": [
    {"id": "sched", "text": "vLLM 调度请求并维护逻辑块表", "level": 0, "span": true},
    {"id": "alloc", "text": "分配/回收事件绑定放置", "level": 1, "lane": 0},
    {"id": "qkv",   "text": "xPU 生成 Q/K/V", "level": 1, "lane": 1},
    ...
  ],
  "edges": [["a","b"], ["a","c","边上标签"]]
}
  lane 决定节点所在泳道列；span:true 的节点横跨全部泳道；level 决定纵向位置。

2) 层级布局（单列或每层多节点并列）——不定义 "lanes"：
  节点用 level(层)/col(层内序) 定位，每层居中排布。

绘制完成后自动做重叠审计（节点×节点、标签×节点、标签×标签、文字越界），
任何重叠都会报错退出——此时缩短标签文字或调整 level/lane 后重画。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCALE = 2  # 2x 超采样，缩小后文字更清晰
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
]


def die(msg: str):
    print(f"[draw] 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size * SCALE, index=2)
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", "Noto Sans CJK SC"],
                             capture_output=True, text=True, check=True).stdout
        return ImageFont.truetype(out.strip(), size * SCALE)
    except Exception:
        die("找不到中文字体（Noto Sans CJK）")


def wrap(text: str, font, max_w):
    """按像素宽度折行（中文逐字、英文整词）。"""
    lines, cur, cur_w = [], "", 0
    for ch in text:
        w = font.getlength(ch)
        if cur_w + w > max_w and cur:
            lines.append(cur)
            cur, cur_w = ch, w
        else:
            cur, cur_w = cur + ch, cur_w + w
    if cur:
        lines.append(cur)
    return lines or [""]


class Flow:
    """布局与绘制。坐标单位为逻辑像素，绘制时乘 SCALE。"""

    FS_NODE, FS_LABEL, FS_TITLE, FS_LANE = 19, 14, 23, 17
    LANE_BOX_W, PLAIN_BOX_W = 300, 420
    GAP_X, GAP_Y, PAD = 46, 44, 36

    def __init__(self, spec):
        self.spec = spec
        self.nodes = {n["id"]: dict(n) for n in spec["nodes"]}
        self.edges = spec.get("edges", [])
        self.f_node = load_font(self.FS_NODE)
        self.f_label = load_font(self.FS_LABEL)
        self.f_title = load_font(self.FS_TITLE)
        self.f_lane = load_font(self.FS_LANE)
        self.lanes = spec.get("lanes")
        # 同层跨列边上若有标签，泳道间距需容纳标签宽度
        self.gap_x = self.GAP_X
        for e in self.edges:
            if len(e) > 2 and e[2] and e[0] in self.nodes and e[1] in self.nodes:
                a, b = self.nodes[e[0]], self.nodes[e[1]]
                if a.get("level") == b.get("level"):
                    self.gap_x = max(self.gap_x,
                                     int(self.f_label.getlength(e[2]) / SCALE) + 26)
        self._layout()

    # ---------------- 布局 ----------------
    def _layout(self):
        sp = self.spec
        lane_mode = self.lanes is not None
        for n in self.nodes.values():
            if lane_mode:
                if "span" not in n and "lane" not in n:
                    die(f"节点 {n.get('id')} 缺少 lane 或 span 字段")
            elif "level" not in n:
                die(f"节点 {n.get('id')} 缺少 level 字段")

        # 折行
        if lane_mode:
            box_w = self.LANE_BOX_W
            n_lane = len(self.lanes)
            span_w = n_lane * box_w + (n_lane - 1) * self.gap_x
            for n in self.nodes.values():
                n["_w"] = span_w if n.get("span") else box_w
        else:
            levels = {}
            for n in self.nodes.values():
                levels.setdefault(n["level"], []).append(n)
            for lv in levels:
                levels[lv].sort(key=lambda n: n.get("col", 0))
            n_col = max(len(v) for v in levels.values())
            box_w = self.PLAIN_BOX_W if n_col == 1 else 260
            for n in self.nodes.values():
                n["_w"] = box_w
        for n in self.nodes.values():
            n["_lines"] = wrap(n["text"], self.f_node, (n["_w"] - 22) * SCALE)
            line_h = (self.FS_NODE + 7) * SCALE
            n["_h"] = max(46, len(n["_lines"]) * line_h // SCALE + 18)

        # 泳道布局：列固定为泳道，纵向按 level
        if lane_mode:
            n_lane = len(self.lanes)
            total_w = n_lane * self.LANE_BOX_W + (n_lane - 1) * self.gap_x
            lane_header_h = 40
            levels = sorted({n["level"] for n in self.nodes.values()})
            ys = {}
            y = self.PAD + (44 if sp.get("title") else 0) + lane_header_h + 14
            for lv in levels:
                h = max(n["_h"] for n in self.nodes.values() if n["level"] == lv)
                ys[lv] = y
                y += h + self.GAP_Y
            for n in self.nodes.values():
                if n.get("span"):
                    x0 = self.PAD
                else:
                    x0 = self.PAD + n["lane"] * (self.LANE_BOX_W + self.gap_x)
                n["_rect"] = [x0, ys[n["level"]], x0 + n["_w"], ys[n["level"]] + n["_h"]]
            W = total_w + self.PAD * 2
            H = y - self.GAP_Y + self.PAD
            self.lane_rects = []
            for i, name in enumerate(self.lanes):
                x0 = self.PAD + i * (self.LANE_BOX_W + self.gap_x)
                self.lane_rects.append([x0, self.PAD + (44 if sp.get("title") else 0),
                                        x0 + self.LANE_BOX_W,
                                        self.PAD + (44 if sp.get("title") else 0) + lane_header_h])
        # 层级布局：每层内居中排布
        else:
            levels_l = {}
            for n in self.nodes.values():
                levels_l.setdefault(n["level"], []).append(n)
            for lv in levels_l:
                levels_l[lv].sort(key=lambda n: n.get("col", 0))
            max_in_row = max(len(v) for v in levels_l.values())
            W = self.PAD * 2 + max_in_row * box_w + (max_in_row - 1) * self.gap_x
            if sp.get("title"):
                W = max(W, int(self.f_title.getlength(sp["title"]) / SCALE) + 2 * self.PAD)
            y = self.PAD + (44 if sp.get("title") else 0)
            for lv in sorted(levels_l):
                ns = levels_l[lv]
                total = len(ns) * box_w + (len(ns) - 1) * self.gap_x
                x = (W - total) / 2
                h = max(n["_h"] for n in ns)
                for n in ns:
                    n["_rect"] = [x, y, x + n["_w"], y + n["_h"]]
                    x += n["_w"] + self.GAP_X
                y += h + self.GAP_Y
            H = y - self.GAP_Y + self.PAD
            self.lane_rects = []

        self.W, self.H = int(W), int(H)

    # ---------------- 重叠审计 ----------------
    def audit(self, label_rects):
        def inter(a, b):
            return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])

        rects = {nid: n["_rect"] for nid, n in self.nodes.items()}
        ids = list(rects)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if inter(rects[ids[i]], rects[ids[j]]):
                    die(f"重叠审计失败：节点 {ids[i]} 与 {ids[j]} 相交。请调整 level/lane")
        for k, lr in label_rects.items():
            for nid, r in rects.items():
                if inter(lr, r):
                    die(f"重叠审计失败：边标签「{k}」与节点 {nid} 相交。请缩短标签或加 level 间距")
        labs = list(label_rects.items())
        for i in range(len(labs)):
            for j in range(i + 1, len(labs)):
                if inter(labs[i][1], labs[j][1]):
                    die(f"重叠审计失败：边标签「{labs[i][0]}」与「{labs[j][0]}」相交")
        for nid, n in self.nodes.items():
            if n["_h"] < len(n["_lines"]) * (self.FS_NODE + 7) + 10:
                die(f"重叠审计失败：节点 {nid} 文字超出框高")
        if self.W > 2000 or self.H > 1400:
            die(f"画布 {self.W}x{self.H} 过大（>2000x1400），图会太长/太宽。请精简节点或改用泳道双列")

    # ---------------- 绘制 ----------------
    def render(self, out_path: Path):
        img = Image.new("RGB", (self.W * SCALE, self.H * SCALE), "white")
        d = ImageDraw.Draw(img)

        def R(v):
            return [x * SCALE for x in v]

        if self.spec.get("title"):
            tw = d.textlength(self.spec["title"], font=self.f_title)
            d.text(((self.W * SCALE - tw) / 2, self.PAD * SCALE * 0.7),
                   self.spec["title"], font=self.f_title, fill="#1A1A1A")

        # 泳道表头
        for name, r in zip(self.lanes or [], self.lane_rects):
            d.rounded_rectangle(R(r), radius=6 * SCALE, fill="#E8F0F8", outline="#3A5F8A",
                                width=SCALE + 1)
            tw = d.textlength(name, font=self.f_lane)
            d.text(((r[0] + r[2]) * SCALE / 2 - tw / 2, (r[1] + r[3]) * SCALE / 2 - self.FS_LANE * SCALE / 1.35),
                   name, font=self.f_lane, fill="#1F3A5F")

        # 边与标签（先画线，节点框再覆盖交叠）
        label_rects = {}
        for e in self.edges:
            a, b = self.nodes[e[0]]["_rect"], self.nodes[e[1]]["_rect"]
            same_row = self.nodes[e[0]].get("level") == self.nodes[e[1]].get("level")
            if same_row and a != b:
                # 同层：从左节点右边中点水平连到右节点左边中点
                if a[0] <= b[0]:
                    ax, bx = a[2], b[0]
                else:
                    ax, bx = a[0], b[2]
                ay = by = (a[1] + a[3]) / 2
                d.line([ax * SCALE, ay * SCALE, bx * SCALE, by * SCALE],
                       fill="#555555", width=SCALE + 1)
                ah = 6
                d.polygon([(bx * SCALE, by * SCALE), ((bx - ah * 1.7) * SCALE, (by - ah) * SCALE),
                           ((bx - ah * 1.7) * SCALE, (by + ah) * SCALE)], fill="#555555")
                if len(e) > 2 and e[2]:
                    label = e[2]
                    tw = d.textlength(label, font=self.f_label) / SCALE
                    lx = (ax + bx) / 2 - tw / 2
                    ly = ay - self.FS_LABEL - 8   # 标签放在水平段上方，白底遮线
                    label_rects[label] = [lx - 4, ly - 3, lx + tw + 4, ly + self.FS_LABEL + 3]
            else:
                # 跨层：底部中点 → 折线 → 顶部中点
                ax, ay = (a[0] + a[2]) / 2, a[3]
                bx, by = (b[0] + b[2]) / 2, b[1]
                midy = ay + (by - ay) * 0.5
                d.line([ax * SCALE, ay * SCALE, ax * SCALE, midy * SCALE,
                        bx * SCALE, midy * SCALE, bx * SCALE, by * SCALE],
                       fill="#555555", width=SCALE + 1)
                ah = 6
                d.polygon([(bx * SCALE, by * SCALE), ((bx - ah) * SCALE, (by - ah * 1.7) * SCALE),
                           ((bx + ah) * SCALE, (by - ah * 1.7) * SCALE)], fill="#555555")
                if len(e) > 2 and e[2]:
                    label = e[2]
                    tw = d.textlength(label, font=self.f_label) / SCALE
                    lx = (ax + bx) / 2 - tw / 2
                    ly = midy - self.FS_LABEL / 1.6 - 3   # 标签放在水平段中点，白底遮线
                    label_rects[label] = [lx - 4, ly - 3, lx + tw + 4, ly + self.FS_LABEL + 3]

        # 先审计算标签位置（不与节点重叠），再落白底与文字
        self.audit(label_rects)
        for label, lr in label_rects.items():
            d.rectangle(R(lr), fill="white")
            d.text((lr[0] * SCALE, lr[1] * SCALE), label, font=self.f_label, fill="#444444")

        # 节点
        for nid, n in self.nodes.items():
            r = n["_rect"]
            fill = n.get("fill", "#F5F8FC" if not n.get("span") else "#EFF5EC")
            d.rounded_rectangle(R(r), radius=7 * SCALE, fill=fill,
                                outline="#3A5F8A" if not n.get("plain") else "#999999",
                                width=SCALE + 1)
            line_h = (self.FS_NODE + 7) * SCALE
            total = line_h * len(n["_lines"])
            ty = ((r[1] + r[3]) * SCALE / 2) - total / 2 + (self.FS_NODE * SCALE) * 0.08
            for li, ln_ in enumerate(n["_lines"]):
                tw = d.textlength(ln_, font=self.f_node)
                d.text(((r[0] + r[2]) * SCALE / 2 - tw / 2, ty + li * line_h),
                       ln_, font=self.f_node, fill="#1A1A1A")

        img.resize((self.W, self.H), Image.LANCZOS).save(out_path)
        ar = self.W / self.H
        if not 0.45 <= ar <= 2.6:
            print(f"[draw] 警告: 长宽比 {ar:.2f} 偏极端，插入文档后排版效果一般", file=sys.stderr)
        print(f"[draw] 完成: {out_path} ({self.W}x{self.H}, 重叠审计通过)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("spec")
    ap.add_argument("out")
    args = ap.parse_args()
    sp = Path(args.spec)
    if not sp.exists():
        die(f"找不到 spec: {sp}")
    Flow(json.loads(sp.read_text(encoding="utf-8"))).render(Path(args.out))


if __name__ == "__main__":
    main()
