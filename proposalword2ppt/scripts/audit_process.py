#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""门禁 A：过程审查。画图之前跑，没过不许动手画。

查的是「有没有按步骤做」，不是「画得好不好」——后者是 audit_quality.py 的事。
六条：
    1. 收过素材（有 index.json，且图片数与包内文件数一致）
    2. 素材分过类（kind 不能还是「未分类」）
    3. 每个图槽都做过「用素材还是自己画」的判断
    4. 用素材的槽，像素够不够铺到这个尺寸；不够要写明重画或放大
    5. 每个图槽都写了尺寸和要讲的一件事
    6. 厂商标识缺失的，记了找过哪些关键词

用法：
    python3 audit_process.py --assets work/assets/index.json \\
            --plan work/figure-plan.json
退出码：0 通过；2 有阻塞项（必须回到对应阶段重做）
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 投影下能看清的有效 DPI 下限。低于这个值，铺到该尺寸就是糊的。
MIN_DPI = 110
# 允许的 source 取值
SOURCES = ("asset", "draw", "external", "pending")
VALID_KINDS = ("厂商标识", "论文或证明", "性能曲线", "架构图", "指标表",
               "经费表", "进度表", "流程图", "其他可用", "用不上")


def effective_dpi(px, cm):
    if not px or not cm:
        return None
    return px / (cm / 2.54)


def audit(assets_path, plan_path):
    blocking, advisory = [], []

    # ---- 1. 素材收集 ----
    if not assets_path or not os.path.exists(assets_path):
        blocking.append(
            "没有素材清单。先跑 collect_assets.py 把申请书和团队资料里的图片、表格"
            "全部收出来——这一步不许跳过，否则现成的架构图和指标表全废了")
        assets = {"images": [], "tables": [], "counts": []}
    else:
        with open(assets_path, encoding="utf-8") as fh:
            assets = json.load(fh)

    images = assets.get("images") or []
    tables = assets.get("tables") or []
    for c in assets.get("counts") or []:
        if c.get("media_in_package", 0) != c.get("images_extracted", 0):
            blocking.append(
                "%s：包内有 %d 个图片文件，只抽出 %d 张，漏了。"
                "docx 的图有 a:blip 和 v:imagedata 两种写法，两种都要抽"
                % (c.get("source"), c.get("media_in_package", 0),
                   c.get("images_extracted", 0)))

    # ---- 2. 素材分类 ----
    if images:
        unclassified = [im for im in images if (im.get("kind") or "未分类") == "未分类"]
        if unclassified:
            blocking.append(
                "%d/%d 张素材图还是「未分类」。逐个看过再标：%s。"
                "不看就不知道哪些能用，只能一律自己画"
                % (len(unclassified), len(images), " / ".join(VALID_KINDS)))
        bad_kind = sorted({im.get("kind") for im in images
                           if im.get("kind") and im.get("kind") not in VALID_KINDS
                           and im.get("kind") != "未分类"})
        if bad_kind:
            advisory.append("分类用了清单外的词：%s。建议归到：%s"
                            % ("、".join(bad_kind), " / ".join(VALID_KINDS)))

    # ---- 3–5. 图表规划 ----
    if not plan_path or not os.path.exists(plan_path):
        blocking.append(
            "没有图表规划。先跑 plan_figures.py 生成骨架，逐槽填 source / type / purpose，"
            "定好每页几张图、每张多大、用素材还是自己画，再动手画")
        return blocking, advisory, {"images": len(images), "tables": len(tables), "slots": 0}

    with open(plan_path, encoding="utf-8") as fh:
        plan = json.load(fh)

    asset_files = {im.get("file") for im in images}
    asset_files |= {os.path.basename(im.get("file") or "") for im in images}
    asset_files |= {tb.get("file") for tb in tables}
    asset_files |= {os.path.basename(tb.get("file") or "") for tb in tables}
    by_file = {}
    for im in images:
        by_file[im.get("file")] = im
        by_file[os.path.basename(im.get("file") or "")] = im

    n_slots = 0
    pages = plan.get("pages") or []
    if not pages:
        blocking.append("图表规划里没有任何页")
    logo_pages = []
    for page in pages:
        pno = page.get("page")
        figs = page.get("figures") or []
        if page.get("kind") in ("cover", "closing"):
            continue
        if not figs:
            blocking.append("第%s页：正文页规划里没有图槽。模板这一页有几个矩形就要规划几张图" % pno)
            continue
        for f in figs:
            n_slots += 1
            slot = f.get("slot") or "?"
            src = (f.get("source") or "").strip()
            w, h = f.get("w"), f.get("h")
            purpose = (f.get("purpose") or "").strip()

            if not src:
                blocking.append("第%s页 %s：没填 source。每个槽都要在"
                                "「用素材(asset) / 自己画(draw) / 外部出图(external)」里选一个"
                                % (pno, slot))
            elif src not in SOURCES:
                blocking.append("第%s页 %s：source=「%s」不是合法取值 %s"
                                % (pno, slot, src, "/".join(SOURCES)))
            if src == "pending":
                advisory.append("第%s页 %s：标了 pending（待补充）。"
                                "只有素材里没有、网上也下不到才允许，生成说明里要写明缺什么"
                                % (pno, slot))
            if not w or not h:
                blocking.append("第%s页 %s：没写尺寸。先按模板量出宽高再画，"
                                "不然半槽里会塞进整槽的内容" % (pno, slot))
            if not purpose:
                blocking.append("第%s页 %s：没写 purpose。一张图只讲一件事，"
                                "写不出这一件事就说明还没想清楚画什么" % (pno, slot))

            if src == "asset":
                ref = (f.get("asset") or "").strip()
                if not ref:
                    blocking.append("第%s页 %s：source=asset 但没填 asset 文件" % (pno, slot))
                elif ref not in asset_files:
                    blocking.append("第%s页 %s：asset「%s」不在素材清单里" % (pno, slot, ref))
                else:
                    im = by_file.get(ref) or by_file.get(os.path.basename(ref)) or {}
                    px_w, px_h = im.get("px_w"), im.get("px_h")
                    if px_w and w:
                        dpi_w = effective_dpi(px_w, w)
                        dpi_h = effective_dpi(px_h, h) if px_h and h else None
                        dpi = min(d for d in (dpi_w, dpi_h) if d) if (dpi_w or dpi_h) else None
                        if dpi and dpi < MIN_DPI:
                            note = (f.get("decision") or "")
                            if not any(k in note for k in ("重画", "放大", "裁", "换")):
                                blocking.append(
                                    "第%s页 %s：%s 铺到 %.1f×%.1f cm 只有 %.0f DPI（< %d），"
                                    "投影下会糊。要么按它的结构重画，要么换一张；"
                                    "决定写进 decision"
                                    % (pno, slot, os.path.basename(ref), w, h, dpi, MIN_DPI))
            if src == "draw":
                if not (f.get("type") or "").strip():
                    blocking.append("第%s页 %s：source=draw 但没填 type（flow/framework/"
                                    "logic/compare/cards/table/bar/pie/gantt/logos）" % (pno, slot))
            if src == "external":
                p = (f.get("asset") or "").strip()
                if not p:
                    blocking.append("第%s页 %s：source=external 但没填出图文件路径" % (pno, slot))
                elif not os.path.exists(p):
                    blocking.append("第%s页 %s：external 图「%s」不存在" % (pno, slot, p))
            if (f.get("type") or "") == "logos" or "厂商" in purpose or "标识" in purpose:
                logo_pages.append((pno, slot, f))

    # ---- 6. 厂商标识 ----
    for pno, slot, f in logo_pages:
        note = (f.get("decision") or "") + (f.get("purpose") or "")
        has_logo_asset = any((im.get("kind") == "厂商标识") for im in images)
        if not has_logo_asset and "搜" not in note and "关键词" not in note:
            advisory.append(
                "第%s页 %s 是厂商标识，但素材里没有标成「厂商标识」的图。"
                "先从素材里找，找不到逐家上网搜官方标识；"
                "哪家搜不到就在 decision 里写明这家是谁、试过什么关键词" % (pno, slot))

    return blocking, advisory, {"images": len(images), "tables": len(tables), "slots": n_slots}


def main():
    ap = argparse.ArgumentParser(description="门禁 A：过程审查（画图前跑）")
    ap.add_argument("--assets", help="collect_assets.py 的 index.json")
    ap.add_argument("--plan", help="plan_figures.py 的 figure-plan.json")
    args = ap.parse_args()

    blocking, advisory, stat = audit(args.assets, args.plan)

    print("过程审查")
    print("  素材：图 %d 张、表 %d 个；规划图槽 %d 个"
          % (stat["images"], stat["tables"], stat["slots"]))
    if blocking:
        print("\n阻塞项（%d）——必须回到对应阶段重做：" % len(blocking))
        for m in blocking:
            print("  [阻塞] %s" % m)
    if advisory:
        print("\n提示项（%d）：" % len(advisory))
        for m in advisory:
            print("  [提示] %s" % m)
    if not blocking:
        print("\n过程审查通过，可以开始画图。")
    else:
        print("\n过程审查未通过：先补齐上面的阻塞项，不要带着问题往下画。")
    return 2 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
