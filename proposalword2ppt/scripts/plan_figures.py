#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按模板导出每页图槽的真实宽高，生成图表规划骨架。

画图之前必须先定「这一页放几张图、每张多大、用素材还是自己画」。
不先定尺寸就画，等于盲画：半槽 4.4 cm 高的位置塞 4 层框架，字只能压到 12 磅。

用法：
    # 1) 看模板每页的图槽尺寸
    python3 plan_figures.py --slots

    # 2) 生成规划骨架（页数按研究内容项数给）
    python3 plan_figures.py --pages 14 --assets work/assets/index.json \\
            --out work/figure-plan.json

骨架里每个图槽一条记录，需要人工填三件事：
    source   用素材（asset）还是自己画（draw）还是外部工具出图（external）
    asset    source=asset 时填 index.json 里的图片/表格文件
    type     source=draw 时填 flow/framework/logic/compare/cards/table/bar/pie/gantt/logos
    purpose  这张图要讲的一件事（一句话）
填完交给 audit_process.py 校验，通过了才动手画。
"""

import argparse
import json
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_ppt  # noqa: E402  复用 PAGE_SLOTS 与蓝图定义

CM_PER_EMU = 1.0 / 360000.0
TEMPLATE = os.path.join(os.path.dirname(HERE), "assets", "template.pptx")

# 每页默认承担什么（与 content-spec 的页面规格一致）
PAGE_ROLE = {
    1: ("cover", "封面", "不配图"),
    2: ("accumulation", "编译领域积累", "槽1厂商标识、槽2量化效果、槽3论文或证明、槽4技术演进"),
    3: ("text-left", "需求分析", "logic 因果链，可加架构对照"),
    4: ("text-left", "研究现状", "framework 模块关系，可加差异表"),
    5: ("figure-left", "研究概述", "framework 总框架"),
    6: ("figure-left", "研究内容一（技术）", "framework，分区与右侧一级标题同名"),
    7: ("figure-left", "研究内容一（过程）", "flow，3–5 步带 children"),
    8: ("figure-left", "研究内容二（技术）", "framework"),
    9: ("figure-left", "研究内容二（过程）", "flow"),
    10: ("figure-left", "主要技术指标", "table"),
    11: ("figure-left", "研究成果及考核方式", "cards"),
    12: ("figure-left", "研究周期与进度安排", "gantt"),
    13: ("figure-left", "经费预算", "pie"),
    14: ("closing", "结束语", "不配图"),
}


def slot_geometry(template=TEMPLATE):
    """从模板量出每页每个图槽的 (名, x, y, 宽, 高)，单位 cm。"""
    out = {}
    with zipfile.ZipFile(template) as z:
        for page, slots in sorted(build_ppt.PAGE_SLOTS.items()):
            bp = build_ppt.PAGE_BLUEPRINT.get(page, page)
            try:
                xml = z.read("ppt/slides/slide%d.xml" % bp).decode("utf-8")
            except KeyError:
                continue
            rows = []
            for name in slots.get("figures", []):
                block = build_ppt.find_shape(xml, name)
                xfrm = build_ppt.shape_xfrm(block) if block is not None else None
                if not xfrm:
                    continue
                rows.append({
                    "slot": name,
                    "x": round(xfrm["x"] * CM_PER_EMU, 2),
                    "y": round(xfrm["y"] * CM_PER_EMU, 2),
                    "w": round(xfrm["cx"] * CM_PER_EMU, 2),
                    "h": round(xfrm["cy"] * CM_PER_EMU, 2),
                })
            out[page] = rows
    return out


def print_slots(geo):
    print("模板图槽实测（cm）。半槽（高 < 6 cm）最多放 3 层或 3 行。\n")
    print("| 页 | 承担 | 图槽 | 位置 | 宽 × 高 | 容量 |")
    print("|---|---|---|---|---|---|")
    for page in sorted(geo):
        role = PAGE_ROLE.get(page, ("", "", ""))
        for i, s in enumerate(geo[page]):
            cap = "整槽" if s["h"] >= 6 else "半槽（≤3 层/行）"
            print("| %d | %s | %s | (%.2f, %.2f) | %.2f × %.2f | %s |" % (
                page, role[1] if i == 0 else "", s["slot"],
                s["x"], s["y"], s["w"], s["h"], cap))


def build_plan(pages, geo, assets_index=None):
    assets = {"images": [], "tables": []}
    if assets_index and os.path.exists(assets_index):
        with open(assets_index, encoding="utf-8") as fh:
            assets = json.load(fh)

    plan = {
        "_说明": "每个图槽一条。source 填 asset/draw/external；"
                 "用 asset 要填 asset 字段，用 draw 要填 type；purpose 一句话。"
                 "填完跑 audit_process.py 校验。",
        "_素材可用": {
            "图片": len(assets.get("images") or []),
            "表格": len(assets.get("tables") or []),
            "清单": assets_index or "（未提供，必须先跑 collect_assets.py）",
        },
        "pages": [],
    }
    for page in range(1, pages + 1):
        role = PAGE_ROLE.get(page, ("figure-left", "（自定）", ""))
        # 研究内容多于两项时，超出的页复用第 8/9 页版式
        geo_page = geo.get(page) or geo.get(9) or []
        if role[0] in ("cover", "closing"):
            plan["pages"].append({
                "page": page, "kind": role[0], "role": role[1],
                "figures": [], "note": "本页不配图",
            })
            continue
        figures = []
        for s in geo_page:
            figures.append({
                "slot": s["slot"],
                "w": s["w"], "h": s["h"],
                "capacity": "整槽" if s["h"] >= 6 else "半槽（≤3 层/行）",
                "source": "",
                "asset": "",
                "type": "",
                "purpose": "",
                "decision": "",
            })
        plan["pages"].append({
            "page": page, "kind": role[0], "role": role[1],
            "default": role[2], "figures": figures,
        })
    return plan


def main():
    ap = argparse.ArgumentParser(description="导出模板图槽尺寸、生成图表规划骨架")
    ap.add_argument("--slots", action="store_true", help="只打印模板图槽尺寸表")
    ap.add_argument("--pages", type=int, default=14, help="总页数（默认 14）")
    ap.add_argument("--assets", help="collect_assets.py 产出的 index.json")
    ap.add_argument("--out", help="规划骨架输出路径")
    ap.add_argument("--template", default=TEMPLATE)
    args = ap.parse_args()

    geo = slot_geometry(args.template)
    if args.slots or not args.out:
        print_slots(geo)
        if not args.out:
            return

    plan = build_plan(args.pages, geo, args.assets)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=2)
    n_slots = sum(len(p["figures"]) for p in plan["pages"])
    print("\n已写出规划骨架：%s" % args.out)
    print("共 %d 页、%d 个图槽待规划。" % (args.pages, n_slots))
    if not args.assets:
        print("[警告] 没给 --assets：必须先跑 collect_assets.py 收素材，"
              "再决定每个槽用素材还是自己画。")


if __name__ == "__main__":
    main()
