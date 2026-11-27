#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频版内容适配：pdf2ppt 的 content.json → 视频版 content_video.json。

为什么要有这一步（用户定稿的三条规则）：
  1. 视频开场已有封面卡（标题+亮点+出处），PPT 的「一.论文概述」页与它内容重复
     ——视频版删除该页，其旁白要点由讲解 agent 并入封面开场白与背景页开场；
  2. PPT 封面页的 汇报人/日期/单位 槽位全部清空（视频里不自我介绍、不报单位）；
  3. 旁白禁止念网址/DOI（信息留在页面要点上）——URL 句机械删除。

本脚本只做机械部分（删页/清字段/删 URL 句/重编号）+ 残留校验；
删除概述页后的旁白衔接、通顺性由讲解 agent 审读改写（改写提示写进 adapt_notes.json）。

用法：
    python3 adapt_content.py --content <PDF名>_ppt/content.json --out <视频工作目录>
    python3 adapt_content.py --content c.json --out . --check     # 只做残留校验
"""

import argparse
import json
import os
import re

URL_RE = re.compile(r"https?://|arxiv\.org|doi\.org|www\.|arXiv:\d{4}\.\d{4,5}")
HOST_RE = re.compile(r"汇报人|本次汇报由|我是.{0,6}来自")
UNIT_HINT = re.compile(r"作者来自|作者单位|来自.{0,12}(大学|研究所|学院|实验室)")


def strip_url_sentences(text):
    """删除含网址/DOI 的整句（信息保留在页面要点上）。返回 (新文本, 删了几句)。"""
    sents = re.split(r"(?<=[。！？；])", text)
    kept, dropped = [], 0
    for s in sents:
        if not s.strip():
            continue
        if URL_RE.search(s):
            dropped += 1
            continue
        kept.append(s)
    out = "".join(kept)
    return (out if re.search(r"[。！？]$", out.strip()) else out.rstrip("，、； ") + "。"), dropped


def adapt(content):
    pages = [dict(p) for p in content["pages"]]
    narr = dict(content["narration"])
    notes = []

    # 1) 删概述页（标题以「一.」开头的第一个 text-left 页）
    drop = None
    for i, p in enumerate(pages):
        if p.get("kind") == "text-left" and str(p.get("title", "")).startswith("一."):
            drop = i
            break
    if drop is not None:
        notes.append("已删除概述页「%s」——其旁白要点（发表信息/方法/结果一句话）"
                     "并入封面开场白与下一页开场，注意衔接通顺" % pages[drop].get("title"))
        old_n = narr.get(str(drop + 1), "")
        notes.append("概述页原旁白（供并入时取材）：%.120s…" % old_n)
        pages.pop(drop)
        # 旁白重编号
        new_narr = {}
        for no in range(1, len(content["pages"]) + 1):
            src_no = no if no <= drop else no + 1
            if no == drop + 1:
                continue
            t = narr.get(str(src_no))
            if t is not None:
                new_narr[str(no - (1 if no > drop + 1 else 0))] = t
        # 上面按位映射易错，直接重算：删除第 drop+1 页后，原第 k 页变成第 k-1 页（k>drop+1）
        new_narr = {}
        for k in range(1, len(content["pages"]) + 1):
            if k == drop + 1:
                continue
            t = narr.get(str(k))
            if t is not None:
                new_narr[str(k - 1 if k > drop + 1 else k)] = t
        narr = new_narr

    # 2) 封面页清汇报人/日期/单位
    if pages and pages[0].get("kind") == "cover":
        for f in ("presenter", "date", "org"):
            if pages[0].get(f):
                pages[0][f] = ""
        notes.append("封面页 presenter/date/org 已清空——旁白与页面都不做汇报人/单位介绍")

    # 3) 旁白删 URL 句
    url_dropped = {}
    for k in list(narr):
        t, n = strip_url_sentences(narr[k])
        if n:
            url_dropped[k] = n
            narr[k] = t
    if url_dropped:
        notes.append("旁白删除网址句 %s——网址信息保留在页面要点上，解说词不念" % url_dropped)

    new_content = {"title": content.get("title", ""),
                   "pages": pages, "narration": narr}
    return new_content, notes


def check_residue(content):
    """适配后残留校验：URL / 汇报人 / 单位提示词。返回问题列表。"""
    bad = []
    for k, t in content["narration"].items():
        if URL_RE.search(t):
            bad.append("第%s页旁白残留网址" % k)
        if HOST_RE.search(t):
            bad.append("第%s页旁白残留汇报人自我介绍" % k)
    cv = content["pages"][0] if content["pages"] else {}
    if any(cv.get(f) for f in ("presenter", "org")):
        bad.append("封面页 presenter/org 未清空")
    return bad


def main():
    ap = argparse.ArgumentParser(description="生成视频版 content_video.json")
    ap.add_argument("--content", required=True, help="pdf2ppt 的 content.json")
    ap.add_argument("--out", required=True, help="视频工作目录")
    ap.add_argument("--check", action="store_true", help="只做残留校验")
    args = ap.parse_args()

    src = json.load(open(args.content))
    if args.check:
        vc_path = os.path.join(os.path.abspath(args.out), "content_video.json")
        vc = json.load(open(vc_path))
        bad = check_residue(vc)
        for b in bad:
            print("[适配][错误] %s" % b)
        raise SystemExit(1 if bad else 0)

    vc, notes = adapt(src)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "content_video.json")
    json.dump(vc, open(out_path, "w"), ensure_ascii=False, indent=1)
    json.dump(notes, open(os.path.join(out_dir, "adapt_notes.json"), "w"),
              ensure_ascii=False, indent=1)
    bad = check_residue(vc)
    print("适配完成：%s（%d 页）" % (out_path, len(vc["pages"])))
    for n in notes:
        print("  [适配] %s" % n)
    for b in bad:
        print("  [适配][错误] %s" % b)
    if bad:
        raise SystemExit(1)
    print("下一步：讲解 agent 按 adapt_notes.json 审读旁白衔接与人话，改完 narration 再跑 --check")


if __name__ == "__main__":
    main()
