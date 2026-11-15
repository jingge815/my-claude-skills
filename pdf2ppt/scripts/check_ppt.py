"""对生成的 pptx 做独立质量审查（用 python-pptx 重新打开成品，与 build_ppt.py 互为交叉验证）。

用法: .venv/bin/python scripts/check_ppt.py 论文总结PPT.pptx --content content.json

错误(E)必须修复（改 content.json 后重新生成）；警告(W)逐条确认后可放行。
覆盖：页数与七节结构、封面/结束页字段、模板占位残留、每个内容页图文都有、
正文要点条数与最小字号、图文不重叠、旁白已写入备注页且与旁白稿一致。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu

CM = 360000
SECTION_ORDER = ["一.论文概述", "二.研究背景", "三.研究问题", "四.传统研究现状",
                 "五.本文研究方法", "六.实验结果", "七.总结"]
KIND_WITH_SUMMARY = ("text-left", "figure-left")
# 模板槽位形状名：这些之外的形状都算「图」的组成部分
SLOT_NAMES = {"文本框 1", "文本框 31", "文本框 33", "矩形 2", "矩形 8",
              "标题 1", "副标题 2", "文本框 3", "文本框 6", "图片 4"}
PLACEHOLDER_RESIDUE = ("（楷体", "（微软雅黑", "（等线", "xxx", "Xxx", "待补充",
                       "1，2句话", "图/表1：", "图1，", "表2：", "{{", "**")
# 致谢规则：最后一页固定「感谢聆听」居中；不许出现批评指正类套话
THANKS_REQUIRED = "感谢聆听"
THANKS_FORBIDDEN = ("批评指正", "欢迎指正", "谢谢大家", "敬请指正")
RED_HEX = "C00000"
MIN_PAGES, MAX_PAGES = 10, 16


def disp_w(text):
    return sum(1.0 if ord(c) > 0x2E7F else 0.55 for c in (text or ""))

errors: list[str] = []
warns: list[str] = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warns.append(msg)


def box(sh):
    if sh.left is None:
        return None
    return (sh.left, sh.top, sh.left + sh.width, sh.top + sh.height)


def intersect(a, b):
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    return max(0, ix) * max(0, iy)


def texts_of(slide):
    out = []
    for sh in slide.shapes:
        if sh.has_text_frame:
            out.append((sh.name, sh.text_frame))
    return out


def check(pptx_path: Path, content: dict):
    pages = content.get("pages") or []
    narration = content.get("narration") or {}

    def narr(i):
        if isinstance(narration, dict):
            return (narration.get(str(i)) or narration.get(i) or "").strip()
        if isinstance(narration, list) and i - 1 < len(narration):
            return (narration[i - 1] or "").strip()
        return ""

    if not pptx_path.name.isascii():
        err(f"输出文件名必须全为英文/数字/连字符：{pptx_path.name}")

    prs = Presentation(str(pptx_path))
    slides = list(prs.slides)
    if len(slides) != len(pages):
        err(f"pptx 有 {len(slides)} 页，content.json 有 {len(pages)} 页，不一致")
    if not MIN_PAGES <= len(slides) <= MAX_PAGES:
        err(f"总页数 {len(slides)}，应在 {MIN_PAGES}–{MAX_PAGES} 页")

    # ---------- 封面 ----------
    if pages and pages[0].get("kind") != "cover":
        err("content.json 第 1 页必须是 cover")
    s1 = slides[0]
    s1_text = " ".join(tf.text for _, tf in texts_of(s1))
    for residue in ("微软雅黑", "xxx", "等线，24"):
        if residue in s1_text:
            err(f"封面有模板占位残留（{residue}），标题/副标题/汇报人未正确填充")
    if not s1_text.strip():
        err("封面没有任何文字")

    # ---------- 结束页 ----------
    if pages and pages[-1].get("kind") != "closing":
        err("content.json 最后一页必须是 closing")

    # ---------- 内容页 ----------
    seq = []
    # 全片字号 sanity：任何 run 超过 50 磅必是字号单位写错（sz 是 1/100 磅，
    # 24 磅写成 2400 会变 2400 磅，一页只剩一个巨型字）。合法上限是封面标题 44 磅。
    for i, slide in enumerate(slides, start=1):
        for sh in slide.shapes:
            if not sh.has_text_frame:
                continue
            for p in sh.text_frame.paragraphs:
                for r in p.runs:
                    if r.font.size is not None and r.font.size.pt > 50:
                        err(f"第{i}页形状「{sh.name}」字号 {r.font.size.pt:g} 磅异常"
                            f"（合法上限 44 磅）——检查 build_ppt 的 sz 单位（1/100 磅）")

    for i, (slide, page) in enumerate(zip(slides, pages), start=1):
        kind = page.get("kind") or ""
        title = page.get("title") or ""
        if kind in ("cover", "closing"):
            continue
        for sec in SECTION_ORDER:
            if title.startswith(sec):
                seq.append(sec)
                break
        else:
            err(f"第{i}页标题「{title[:24]}」没有以七节之一开头")

        all_text = " ".join(tf.text for _, tf in texts_of(slide))
        for residue in PLACEHOLDER_RESIDUE:
            if residue in all_text:
                err(f"第{i}页有模板占位残留「{residue}」，槽位未正确填充")

        # 图文都有：图片 ≥1，或非槽位形状 ≥4（自绘形状图）
        pics, fig_shapes, body_sh, summary_sh, title_sh = [], [], None, None, None
        for sh in slide.shapes:
            if sh.shape_type == 13:  # PICTURE
                pics.append(sh)
            elif sh.name not in SLOT_NAMES:
                fig_shapes.append(sh)
            elif sh.name == "文本框 1" or sh.name == "标题 1":
                title_sh = sh
            elif sh.name == "文本框 31":
                body_sh = sh
            elif sh.name == "文本框 33":
                summary_sh = sh
        if not pics and len(fig_shapes) < 4:
            err(f"第{i}页没有检测到图（无图片、自绘形状仅 {len(fig_shapes)} 个），每个内容页都要图文都有")
        if body_sh is None:
            err(f"第{i}页找不到正文槽（文本框 31）")
        else:
            paras = [p.text for p in body_sh.text_frame.paragraphs if p.text.strip()]
            if len(paras) < 3:
                err(f"第{i}页正文只有 {len(paras)} 条要点，至少 3 条")
            # 标题式要点：一级主句 ≤24 字宽、二级 ≤22、层级 ≤2、不许句号
            n_red = 0
            for p in body_sh.text_frame.paragraphs:
                t = p.text.strip()
                if not t:
                    continue
                if p.level >= 2:
                    err(f"第{i}页出现第 {p.level + 1} 级标题（最多 2 级：标签主句 + 二级说明）")
                limit = 24 if p.level == 0 else 22
                if disp_w(t) > limit + 4:
                    err(f"第{i}页要点「{t[:18]}」{disp_w(t):.0f} 字宽——要写成标题式短语，长句放旁白")
                elif disp_w(t) > limit:
                    warn(f"第{i}页要点「{t[:18]}」{disp_w(t):.0f} 字宽，接近上限，建议再短")
                if "。" in t:
                    err(f"第{i}页要点「{t[:14]}…」含句号：页面上写短语，完整句子放旁白讲")
                for r in p.runs:
                    c = r.font.color
                    if c is not None and c.type is not None and str(c.rgb) == RED_HEX:
                        n_red += 1
            if n_red == 0:
                err(f"第{i}页没有红粗强调——关键数字/方法名/结论词至少标一处（content.json 用 {{{{…}}}}）")
            # 最小字号
            min_pt = None
            for p in body_sh.text_frame.paragraphs:
                for r in p.runs:
                    if r.font.size is not None:
                        pt = r.font.size.pt
                        min_pt = pt if min_pt is None else min(min_pt, pt)
            if min_pt is not None and min_pt < 12:
                err(f"第{i}页正文最小字号 {min_pt} 磅，低于 12 磅下限")
            # 溢出估算：正文固定 18/16 磅，按框宽框高估行数（粗估，明显溢出才报）
            if body_sh.width and body_sh.height:
                w_cm = body_sh.width / CM
                h_cm = body_sh.height / CM
                need = 0.0
                for p in body_sh.text_frame.paragraphs:
                    t = p.text.strip()
                    if not t:
                        continue
                    pt = 18 if p.level == 0 else 16
                    indent = 1.0 if p.level == 0 else 2.13  # ▶/● 项目符号缩进（template-spec）
                    cjk_w = pt / 72 * 2.54
                    cap = max(4, int((w_cm - indent - 0.3) / cjk_w * 1.05))
                    disp = sum(1.0 if ord(c) > 0x2E7F else 0.55 for c in t)
                    need += math.ceil(disp / cap) * (pt * 1.32 / 72 * 2.54 + 0.22)
                ratio = need / max(h_cm, 0.1)
                if ratio > 1.25:
                    err(f"第{i}页正文估算需 {need:.1f} cm，框高 {h_cm:.1f} cm，严重溢出，必须精简要点")
                elif ratio > 1.05:
                    warn(f"第{i}页正文估算需 {need:.1f} cm，接近框高 {h_cm:.1f} cm，建议精简")
        if kind in KIND_WITH_SUMMARY and summary_sh is not None:
            if not summary_sh.text_frame.text.strip():
                err(f"第{i}页总结句为空（{kind} 页应有本页一句话结论）")

        # 图文不重叠（正文框 vs 图片/图形状）
        if body_sh is not None and body_sh.left is not None:
            b = box(body_sh)
            for sh in pics + fig_shapes:
                fb = box(sh)
                if fb is None:
                    continue
                ov = intersect(b, fb)
                if ov > 0.2 * CM * CM:  # 容差 0.2 cm²
                    err(f"第{i}页正文与图「{sh.name}」重叠 {ov/CM/CM:.2f} cm²")
                    break

        # 备注页旁白
        if slide.has_notes_slide:
            got = slide.notes_slide.notes_text_frame.text.strip()
            want = narr(i)
            if want and re.sub(r"\s+", "", got) != re.sub(r"\s+", "", want):
                err(f"第{i}页备注旁白与 content.json 不一致（备注 {len(got)} 字，应为 {len(want)} 字）")
            elif not want:
                warn(f"第{i}页 content.json 没有旁白，备注页为空")
        else:
            err(f"第{i}页没有备注页，旁白未写入")

    # ---------- 七节结构 ----------
    order_idx = [SECTION_ORDER.index(s) for s in seq]
    if order_idx != sorted(order_idx):
        err(f"章节顺序错乱：{' → '.join(seq)}")
    for sec in SECTION_ORDER:
        n = seq.count(sec)
        if n == 0:
            err(f"缺少章节「{sec}」")
        elif sec.startswith("五.") and n < 2:
            err(f"「{sec}」只有 {n} 页，至少要原理页 + 步骤页 2 页")
        elif sec.startswith("六.") and n < 2:
            err(f"「{sec}」只有 {n} 页，至少要主结果 + 分析 2 页")

    # ---------- 致谢：最后一页固定「感谢聆听」，禁止批评指正类套话 ----------
    if slides:
        last_text = " ".join(tf.text for _, tf in texts_of(slides[-1]))
        if THANKS_REQUIRED not in last_text:
            err(f"最后一页缺少「{THANKS_REQUIRED}」（居中，只此一句，不写批评指正类套话）")
    for i, slide in enumerate(slides, 1):
        all_text = " ".join(tf.text for _, tf in texts_of(slide))
        hit = [w for w in THANKS_FORBIDDEN if w in all_text]
        if hit:
            err(f"第{i}页出现套话「{'、'.join(hit)}」——致谢只写「感谢聆听」")

    # ---------- 旁白总量（以 content.json 为准） ----------
    total = sum(len(narr(i)) for i in range(1, len(pages) + 1))
    print(f"[check] 旁白总字数 {total} 字（约 {total/200.0:.1f} 分钟，上限 15 分钟）")
    if total > 3000:
        err(f"旁白总字数 {total} 超过 3000 字（15 分钟讲不完）")
    elif total < 2400:
        err(f"旁白总字数 {total} 少于 2400 字，内容太薄")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pptx")
    ap.add_argument("--content", required=True, help="content.json 路径")
    args = ap.parse_args()
    pptx = Path(args.pptx)
    if not pptx.exists():
        print(f"[check] 错误: 找不到 {pptx}", file=sys.stderr)
        sys.exit(2)
    content = json.loads(Path(args.content).read_text(encoding="utf-8"))
    check(pptx, content)
    for w in warns:
        print(f"W  {w}")
    for e in errors:
        print(f"E  {e}")
    print(f"\n[check] 错误 {len(errors)} 项，警告 {len(warns)} 项")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
