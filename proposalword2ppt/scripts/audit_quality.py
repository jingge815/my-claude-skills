#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""门禁 B：质量审查。生成 pptx 之后跑，没过不许交付。

查的是成品本身，八条：
    1. 页数 ≥ 14
    2. 同一页里图与图、图与正文不重叠
    3. 图内部的框与文字不互相遮挡、不出框
    4. 没有「待补充」「图/表」这类占位
    5. 每张图的尺寸、布局、字号在规定范围内
    6. 底部总结句概括本页核心，不是空泛判断
    7. 研究内容第 1 页：每个技术项都写了传统不足 + 设计或提出的方法
    8. 第 4 页研究现状：写了已有方法的缺陷

用法：
    python3 audit_quality.py --pptx 答辩PPT.pptx --content content.json
退出码：0 通过；2 有阻塞项（按提示回到对应阶段重做）

正文溢出按真实折行量算（需要 Pillow），不靠字数估。
"""

import argparse
import json
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import shapes as _shapes
except ImportError:
    _shapes = None

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
EMU = 360000.0

MIN_PAGES = 14
SLIDE_W, SLIDE_H = 33.87, 19.05
# 形状重叠容差：描边有宽度，共边会算出细丝交集，两个方向都超过才算遮挡
OVERLAP_MIN_CM = 0.12
MIN_FIG_PT = 14.0
MAX_FIG_PT = 18.0
MIN_BODY_PT = 12.0
PLACEHOLDER_WORDS = ("待补充", "图/表", "xxx", "XXX", "（待补）", "TODO")

# 第 7 条：研究内容第 1 页，每个技术项要讲清「传统不足」与「我们设计/提出了什么」。
# 不要求出现「创新」二字——创新靠「设计/提出了什么方法」本身体现，写上「创新」反而是空话。
# 「不足」的说法很多：无/未/难/缺/不可用/低效 都是，词表窄了会把写对的内容判成没写。
# 宁可词表宽一点（漏判好过误拦），真正没写不足的文字里一个否定词都不会有。
LACK_WORDS = ("未", "无", "缺", "难", "不足", "无法", "不支持", "没有", "停留",
              "受限", "失效", "不成立", "开销大", "不适配", "瓶颈", "不可",
              "不能", "低效", "冗余", "空白", "尚不", "欠", "受制", "不一致")
METHOD_WORDS = ("设计", "提出", "建立", "构建", "实现", "研制", "突破",
                "研究", "形成", "引入", "定义")
# 第 8 条：研究现状要写已有方法的缺陷
GAP_WORDS = LACK_WORDS + ("尚不成熟", "不够", "有待", "欠缺", "空白")
# 第 6 条：总结句不能是这种谁都能说的空话
VAGUE_SUMMARY = ("意义重大", "非常重要", "十分必要", "前景广阔", "效果良好",
                 "具有重要意义", "很有价值", "值得推广")

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/kaiti/KaiTi.ttf",
    "/usr/share/fonts/truetype/arphic/ukai.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/simkai.ttf",
]


# ---------------------------------------------------------------- 真实折行
class _Wrapper(object):
    """按字体实际宽度算折行，不用字数估。拿不到字体就退回等宽近似。"""

    def __init__(self, dpi=200):
        self.dpi = dpi
        self.draw = None
        self.path = next((c for c in FONT_CANDIDATES if os.path.exists(c)), None)
        self.cache = {}
        try:
            from PIL import Image, ImageDraw
            self.draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        except ImportError:
            self.draw = None

    @property
    def exact(self):
        return self.draw is not None and self.path is not None

    def _font(self, pt):
        from PIL import ImageFont
        key = round(pt, 1)
        if key not in self.cache:
            px = max(8, int(round(pt / 72.0 * self.dpi)))
            try:
                self.cache[key] = ImageFont.truetype(self.path, px)
            except Exception:
                self.cache[key] = ImageFont.load_default()
        return self.cache[key]

    def lines(self, text, pt, width_cm, first_width_cm=None):
        if not text:
            return 0
        if not self.exact:
            # 近似：CJK 按一个字宽 pt，ASCII 折半
            disp = sum(0.55 if ord(c) < 128 else 1.0 for c in text)
            cpl = max(2.0, (width_cm - 0.1) / (pt * 0.0353))
            return max(1, int((disp + cpl - 0.01) / cpl))
        f = self._font(pt)
        lim_first = (first_width_cm if first_width_cm is not None else width_cm) * self.dpi / 2.54
        lim_rest = width_cm * self.dpi / 2.54
        n, cur, limit = 0, "", lim_first
        for ch in text:
            if ch == "\n":
                n += 1
                cur, limit = "", lim_rest
                continue
            if self.draw.textlength(cur + ch, font=f) <= limit or not cur:
                cur += ch
            else:
                n += 1
                cur, limit = ch, lim_rest
        if cur:
            n += 1
        return n


# ---------------------------------------------------------------- pptx 读取
def _xfrm(sp):
    x = sp.find("./" + P + "spPr/" + A + "xfrm")
    if x is None:
        x = sp.find("./" + P + "grpSpPr/" + A + "xfrm")
    if x is None:
        return None
    off, ext = x.find(A + "off"), x.find(A + "ext")
    if off is None or ext is None:
        return None
    try:
        return (int(off.get("x")) / EMU, int(off.get("y")) / EMU,
                int(ext.get("cx")) / EMU, int(ext.get("cy")) / EMU)
    except (TypeError, ValueError):
        return None


def _spacing_pt(ppr, tag, font_pt):
    """段前/段后间距，换算成磅。spcPts 是绝对磅值，spcPct 是字号的百分比。"""
    if ppr is None:
        return 0.0
    el = ppr.find(A + tag)
    if el is None:
        return 0.0
    pts = el.find(A + "spcPts")
    if pts is not None:
        try:
            return int(pts.get("val")) / 100.0
        except (TypeError, ValueError):
            return 0.0
    pct = el.find(A + "spcPct")
    if pct is not None:
        try:
            return font_pt * int(pct.get("val")) / 100000.0
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _line_factor(ppr):
    """行距系数。缺省按 1.2 倍行高算。"""
    if ppr is None:
        return 1.2
    el = ppr.find(A + "lnSpc")
    if el is None:
        return 1.2
    pct = el.find(A + "spcPct")
    if pct is not None:
        try:
            return max(0.8, int(pct.get("val")) / 100000.0)
        except (TypeError, ValueError):
            return 1.2
    return 1.2


def _paragraphs(sp):
    """返回 [(文本, 字号, 左缩进cm, 首行缩进cm, 段前pt, 段后pt, 行距系数)]。

    间距必须从 XML 里读，不能假定每段都有 8 磅段后：模板里封面四行、
    结束语感谢语都没有 spcAft，按统一值加会凭空多算出一厘米多，
    把放得下的页判成溢出。
    """
    out = []
    body = sp.find("./" + P + "txBody")
    if body is None:
        return out
    for para in body.findall(A + "p"):
        runs = para.findall(A + "r")
        text = "".join("".join(t.text or "" for t in r.findall(A + "t")) for r in runs)
        if not text.strip():
            continue
        pt = None
        for r in runs:
            rpr = r.find(A + "rPr")
            if rpr is not None and rpr.get("sz"):
                pt = int(rpr.get("sz")) / 100.0
                break
        ppr = para.find(A + "pPr")
        marL = ind = 0.0
        if ppr is not None:
            try:
                marL = int(ppr.get("marL") or 0) / EMU
            except ValueError:
                marL = 0.0
            try:
                ind = int(ppr.get("indent") or 0) / EMU
            except ValueError:
                ind = 0.0
        base = pt or 18.0
        out.append((text, pt, marL, ind,
                    _spacing_pt(ppr, "spcBef", base),
                    _spacing_pt(ppr, "spcAft", base),
                    _line_factor(ppr)))
    return out


def _autofits(sp):
    """文本框的自适应方式：spAutoFit 表示 PowerPoint 会把框撑大，不算溢出。"""
    body = sp.find("./" + P + "txBody")
    if body is None:
        return ""
    bpr = body.find(A + "bodyPr")
    if bpr is None:
        return ""
    for tag in ("spAutoFit", "normAutofit", "noAutofit"):
        if bpr.find(A + tag) is not None:
            return tag
    return ""


def read_slides(pptx):
    """每页：[{name, box, texts, kind}]，kind 区分正文框/图形状/图片。"""
    slides = []
    with zipfile.ZipFile(pptx) as z:
        names = sorted(
            (n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
            key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        for n in names:
            root = ET.fromstring(z.read(n))
            tree = root.find("./" + P + "cSld/" + P + "spTree")
            items = []
            if tree is None:
                slides.append(items)
                continue
            for sp in list(tree):
                tag = sp.tag
                if tag not in (P + "sp", P + "pic", P + "graphicFrame", P + "grpSp"):
                    continue
                nv = sp.find(".//" + P + "cNvPr")
                name = (nv.get("name") if nv is not None else "") or ""
                box = _xfrm(sp)
                if box is None:
                    continue
                texts = _paragraphs(sp) if tag == P + "sp" else []
                items.append({"name": name, "box": box, "texts": texts,
                              "is_pic": tag == P + "pic",
                              "autofit": _autofits(sp) if tag == P + "sp" else ""})
            slides.append(items)
    return slides


def _inter(a, b):
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return (max(0.0, w), max(0.0, h))


def _contains(outer, inner, tol=0.05):
    return (outer[0] - tol <= inner[0] and outer[1] - tol <= inner[1]
            and outer[0] + outer[2] + tol >= inner[0] + inner[2]
            and outer[1] + outer[3] + tol >= inner[1] + inner[3])


# ---------------------------------------------------------------- 各条检查
def check_pages(slides, blocking):
    if len(slides) < MIN_PAGES:
        blocking.append(
            "只有 %d 页，少于 %d 页。页数由研究内容项数决定：每项连续 2–3 页。"
            "不足就回到图表规划，按申请书的研究内容把页补齐" % (len(slides), MIN_PAGES))


def check_placeholders(slides, blocking):
    for i, items in enumerate(slides, 1):
        for it in items:
            for para in it["texts"]:
                text = para[0]
                for w in PLACEHOLDER_WORDS:
                    if w in text:
                        blocking.append(
                            "第%d页 %s 还留着占位「%s」。每页都要有真图：先从素材里找，"
                            "找不到自己画；确实做不出的要在生成说明里写明缺什么"
                            % (i, it["name"], w))
                        break


def check_slide_overlap(slides, blocking):
    """图与图、图与正文不许压在一起。

    同一组图内部的层/格子本来就互相包含，所以按「正文框 vs 图元素」和
    「不同图组的外接框」两个粒度查，不查同组内部（那是 shape_overlaps 的事）。
    """
    for i, items in enumerate(slides, 1):
        bodies = [it for it in items
                  if it["texts"] and it["name"].startswith("文本框")]
        figs = [it for it in items
                if it["is_pic"] or not it["name"].startswith(("文本框", "标题", "副标题"))]
        for b in bodies:
            for f in figs:
                w, h = _inter(b["box"], f["box"])
                if w > OVERLAP_MIN_CM and h > OVERLAP_MIN_CM:
                    blocking.append(
                        "第%d页：正文框「%s」与图元素「%s」重叠 %.2f×%.2f cm。"
                        "正文只占文字槽、图只占图槽；回到图表规划调槽位或收窄正文"
                        % (i, b["name"], f["name"], w, h))
        for it in items:
            x, y, w, h = it["box"]
            if x < -0.5 or y < -0.5 or x + w > SLIDE_W + 0.5 or y + h > SLIDE_H + 0.5:
                blocking.append(
                    "第%d页：「%s」超出页面（%.2f, %.2f, %.2f×%.2f，页面 %.1f×%.1f）"
                    % (i, it["name"], x, y, w, h, SLIDE_W, SLIDE_H))


def check_body_overflow(slides, blocking, advisory, wrapper):
    """正文按真实折行高度核对，不靠字数估。

    间距逐段从 XML 读；最后一段的段后间距不占框内高度；
    spAutoFit 的框由 PowerPoint 自己撑大，不按固定框高判溢出。
    """
    for i, items in enumerate(slides, 1):
        for it in items:
            if not it["texts"] or not it["name"].startswith("文本框"):
                continue
            x, y, bw, bh = it["box"]
            if bw < 3 or bh < 1.5:
                continue
            if it.get("autofit") == "spAutoFit":
                continue
            total = 0.0
            n_para = len(it["texts"])
            for k, (text, pt, marL, ind, bef, aft, lf) in enumerate(it["texts"]):
                pt = pt or 18.0
                if pt < MIN_BODY_PT - 0.01:
                    blocking.append("第%d页 %s：字号 %.0f 磅低于 %.0f 磅下限"
                                    % (i, it["name"], pt, MIN_BODY_PT))
                body_w = max(1.0, bw - max(0.0, marL) - 0.1)
                first_w = max(1.0, bw - max(0.0, marL + ind) - 0.1)
                n = wrapper.lines(text, pt, body_w, first_w)
                total += n * pt * lf / 28.35
                total += bef / 28.35
                if k < n_para - 1:        # 末段的段后间距不占框内高度
                    total += aft / 28.35
            fix = ("三条路按顺序试：缩减字数、加宽文本框、降字号（不低于 %.0f 磅）"
                   % MIN_BODY_PT)
            if total > bh * 1.02:
                blocking.append(
                    "第%d页 %s：正文折行后约 %.2f cm，框高只有 %.2f cm，会溢出。%s"
                    % (i, it["name"], total, bh, fix))
            elif total > bh - 0.1:
                # 本机未必装着模板用的楷体，折行宽度是用替代字体量的，
                # 贴边的这点差值可能是字体差异。报成提示，让人去预览图上确认。
                advisory.append(
                    "第%d页 %s：正文约 %.2f cm，框高 %.2f cm，贴着边。"
                    "换字体后可能溢出，到预览图上确认；真溢出就%s"
                    % (i, it["name"], total, bh, fix))


def check_figure_geometry(content, blocking, advisory):
    """图内部：框压框、字出框、字号、空白占比。"""
    if _shapes is None or not content:
        return
    pages = content.get("pages") or []
    # 饼图这类图形天然稀疏（圆只占外接框约 78%），单独放宽
    SPARSE_TYPES = ("pie", "bar", "gantt", "logos")
    for idx, page in enumerate(pages, 1):
        raw = page.get("figure")
        figs = raw if isinstance(raw, list) else ([raw] if raw else [])
        slots = _slot_sizes(idx)
        for k, fig in enumerate(figs):
            if not isinstance(fig, dict):
                continue
            spec = dict(fig)
            if k < len(slots):
                spec["_w"], spec["_h"] = slots[k]
            cv = _shapes.render_shapes(spec)
            if cv is None:
                continue
            label = "第%d页第%d组图（%s）" % (idx, k + 1, fig.get("type") or "image")
            for name, text, need, have in _shapes.text_overflows(cv):
                blocking.append("%s：「%s」文字需要 %.2f cm、框高 %.2f cm，字被截。"
                                "缩短这条文字或换更矮的图型" % (label, text[:18], need, have))
            for a, b, w, h in _shapes.shape_overlaps(cv):
                blocking.append("%s：「%s」与「%s」重叠 %.2f×%.2f cm，图内有遮挡。"
                                "减少节点或加大间距" % (label, a, b, w, h))
            for name, x0, y0, x1, y1 in _shapes.out_of_canvas(cv):
                blocking.append("%s：「%s」出框（%.2f, %.2f）–（%.2f, %.2f）"
                                % (label, name, x0, y0, x1, y1))
            for it in cv.items:
                for t in (it.get("texts") or []):
                    pt = t[1] if len(t) > 1 else None
                    if pt and pt < MIN_FIG_PT - 0.01:
                        advisory.append("%s：「%s」字号 %.0f 磅低于 %.0f 磅，"
                                        "投影下偏小，缩短文字换取字号"
                                        % (label, str(t[0])[:14], pt, MIN_FIG_PT))
                    if pt and pt > MAX_FIG_PT + 0.01:
                        advisory.append("%s：「%s」字号 %.0f 磅超过 %.0f 磅上限"
                                        % (label, str(t[0])[:14], pt, MAX_FIG_PT))
            blank = _shapes.blank_ratio(cv)
            limit = 0.70 if (fig.get("type") in SPARSE_TYPES) else 0.45
            if blank > limit:
                advisory.append("%s：空白占 %.0f%%，图没撑满这个槽。"
                                "补节点或子步骤，别让图缩在一角" % (label, blank * 100))


def _slot_sizes(index):
    try:
        import build_ppt
    except ImportError:
        return []
    import zipfile as _zip
    tpl = os.path.join(os.path.dirname(HERE), "assets", "template.pptx")
    if not os.path.exists(tpl):
        return []
    slots = build_ppt.PAGE_SLOTS.get(index, {}).get("figures", [])
    if not slots:
        return []
    bp = build_ppt.PAGE_BLUEPRINT.get(index, index)
    out = []
    with _zip.ZipFile(tpl) as z:
        try:
            xml = z.read("ppt/slides/slide%d.xml" % bp).decode("utf-8")
        except KeyError:
            return []
        for name in slots:
            block = build_ppt.find_shape(xml, name)
            x = build_ppt.shape_xfrm(block) if block is not None else None
            if x:
                out.append((x["cx"] / EMU, x["cy"] / EMU))
    return out


def check_summary(content, blocking, advisory):
    """第 6 条：总结句要概括本页核心，不是空泛判断。"""
    for idx, page in enumerate((content.get("pages") or []), 1):
        kind = page.get("kind")
        if kind == "cover":
            continue
        summary = (page.get("summary") or "").strip()
        title = page.get("title") or ""
        # 模板第 3、4 页没有总结槽，不强求
        if kind == "text-left":
            continue
        if not summary:
            blocking.append("第%d页（%s）没有底部总结句。总结句要落在"
                            "「这一页做完得到什么」上" % (idx, title))
            continue
        for w in VAGUE_SUMMARY:
            if w in summary:
                blocking.append("第%d页总结句含空话「%s」：%s。"
                                "换成这一页的具体结论" % (idx, w, summary[:28]))
                break
        if summary.startswith("针对"):
            blocking.append("第%d页总结句套了「针对……」：%s" % (idx, summary[:28]))
        # 概括性：总结句里的关键词要能在本页要点里找到，否则是另起一句口号
        points = page.get("points") or []
        body = ""
        for p in points:
            if isinstance(p, dict):
                body += (p.get("label") or "") + (p.get("text") or "")
                body += "".join(p.get("subs") or [])
            else:
                body += str(p)
        if body:
            # \u4e2d\u6587\u4e0d\u5206\u8bcd\uff0c\u6574\u6bb5\u53d6\u51fa\u7684\u300c\u957f\u8bcd\u300d\u51e0\u4e4e\u4e0d\u53ef\u80fd\u539f\u6837\u51fa\u73b0\u5728\u8981\u70b9\u91cc\uff0c
            # \u6309 2 \u5b57\u7247\u6bb5\u6bd4\u5bf9\u624d\u53cd\u6620\u300c\u8bf4\u7684\u662f\u4e0d\u662f\u540c\u4e00\u4ef6\u4e8b\u300d\u3002
            keys = set()
            for run in re.findall(r"[\u4e00-\u9fa5]{2,}", summary):
                for j in range(len(run) - 1):
                    keys.add(run[j:j + 2])
            for w in re.findall(r"[A-Za-z]{2,}", summary):
                keys.add(w)
            hit = sum(1 for w in keys if w in body)
            if keys and hit < max(2, int(len(keys) * 0.15)):
                advisory.append("第%d页总结句和本页要点没有共同关键词：%s。"
                                "总结句要概括本页内容，不是另起一句口号"
                                % (idx, summary[:28]))


def check_research_pages(content, blocking):
    """第 7 条：研究内容第 1 页，每个技术项都要写传统不足 + 设计/提出的方法。

    不检查、也不要求出现「创新」二字：创新由「设计/提出了什么方法」本身体现。
    """
    pages = content.get("pages") or []
    seen = {}
    for idx, page in enumerate(pages, 1):
        title = page.get("title") or ""
        m = re.match(r"研究内容([一二三四五六七八九十\d]+)", title)
        if not m:
            continue
        key = m.group(1)
        seen.setdefault(key, []).append((idx, page))
    for key, group in seen.items():
        idx, page = group[0]            # 该研究内容的第 1 页
        points = page.get("points") or []
        if not points:
            blocking.append("第%d页（研究内容%s 第 1 页）没有技术项要点" % (idx, key))
            continue
        for p in points:
            if isinstance(p, dict):
                label = p.get("label") or ""
                text = p.get("text") or ""
                subs = p.get("subs") or []
            else:
                label, text, subs = "", str(p), []
            blob = text + "".join(subs)
            name = label or text[:12]
            if not any(w in blob for w in LACK_WORDS):
                blocking.append(
                    "第%d页「%s」没写传统做法的不足。研究内容第 1 页每个技术项要写清："
                    "传统做法哪里不行 → 我们设计/提出了什么方法 → 带来什么"
                    % (idx, name))
            if not any(w in blob for w in METHOD_WORDS):
                blocking.append(
                    "第%d页「%s」没写我们设计或提出的方法。用「设计了/提出了/建立了"
                    "〈方法名〉」把做法写实，不要只说现状，也不必写「创新」二字"
                    % (idx, name))


def check_status_page(content, blocking):
    """第 8 条：第 4 页研究现状必须写已有方法的缺陷。"""
    pages = content.get("pages") or []
    for idx, page in enumerate(pages, 1):
        title = page.get("title") or ""
        if "研究现状" not in title:
            continue
        points = page.get("points") or []
        blob = ""
        per_point = []
        for p in points:
            if isinstance(p, dict):
                one = (p.get("text") or "") + "".join(p.get("subs") or [])
                per_point.append(((p.get("label") or one[:12]), one))
            else:
                one = str(p)
                per_point.append((one[:12], one))
            blob += one
        if not any(w in blob for w in GAP_WORDS):
            blocking.append(
                "第%d页研究现状没写已有方法的缺陷。每个模块都要写它的职能和不足，"
                "总述里还要写总体缺什么" % idx)
            continue
        # 除总述外，每个模块都要有不足
        lacking = [name for name, one in per_point[1:]
                   if not any(w in one for w in GAP_WORDS)]
        if lacking:
            blocking.append(
                "第%d页研究现状的「%s」只写了职能、没写不足。研究现状页的每个模块"
                "都要点出已有方法缺什么" % (idx, "、".join(lacking[:3])))


def main():
    ap = argparse.ArgumentParser(description="门禁 B：质量审查（生成后跑）")
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--content", help="content.json，查内容类规则")
    args = ap.parse_args()

    blocking, advisory = [], []
    slides = read_slides(args.pptx)
    wrapper = _Wrapper()

    check_pages(slides, blocking)
    check_placeholders(slides, blocking)
    check_slide_overlap(slides, blocking)
    check_body_overflow(slides, blocking, advisory, wrapper)

    content = None
    if args.content and os.path.exists(args.content):
        with open(args.content, encoding="utf-8") as fh:
            content = json.load(fh)
        check_figure_geometry(content, blocking, advisory)
        check_summary(content, blocking, advisory)
        check_research_pages(content, blocking)
        check_status_page(content, blocking)
    else:
        advisory.append("没给 --content，内容类规则（总结句、研究内容页、研究现状页）未检查")

    print("质量审查")
    print("  页数 %d；折行计算：%s" % (len(slides), "精确" if wrapper.exact else "近似（缺字体或 Pillow）"))
    if blocking:
        print("\n阻塞项（%d）——必须改掉再交付：" % len(blocking))
        for m in blocking:
            print("  [阻塞] %s" % m)
    if advisory:
        print("\n提示项（%d）：" % len(advisory))
        for m in advisory:
            print("  [提示] %s" % m)
    if not blocking:
        print("\n质量审查通过。")
    else:
        print("\n质量审查未通过：按提示回到对应阶段重做，不要带着问题交付。")
    return 2 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
