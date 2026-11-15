#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 proposalword2ppt 的模板生成 pdf2ppt 的论文总结模板（维护脚本）。

新模板 assets/template.pptx 只保留 5 张蓝图页，全部沿用原模板的版式、
背景、ISCAS 标识、字体与配色，只调整槽位几何与占位说明文字：

  蓝图1 cover       ← 原 slide1（封面）          标题/副标题/汇报人三行
  蓝图2 text-left   ← 原 slide5 镜像（文左图右）  正文左、1 图槽右、底部总结
  蓝图3 status      ← 原 slide4（研究现状）      正文左、2 图槽右（上下）
  蓝图4 figure-left ← 原 slide5（图左文右）      1 图槽左、正文右、底部总结
  蓝图5 closing     ← 原 slide14（结束页）       一句话点题 + 感谢语 + 标识

改动模板后重跑本脚本即可；几何若变，须同步改 build_ppt.py 的
KIND_SLOTS / FIGURE_* / SUMMARY_* 常量与 references/template-spec.md。

用法：python3 rebuild_template.py [--src 原模板.pptx] [--out ../assets/template.pptx]
"""
import argparse
import re
import sys
import zipfile

CM = 360000  # EMU per cm
XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

CANVAS_W = 33.87  # cm，正文槽镜像时按画布宽取对称位置


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def find_shape(xml, name):
    for m in re.finditer(r"<p:sp>.*?</p:sp>", xml, re.S):
        if 'name="%s"' % name in m.group(0):
            return m.group(0)
    return None


def set_text(xml, name, lines):
    """把形状文本整体替换为 lines（每行一个段落），沿用原第一段的 run 属性。"""
    block = find_shape(xml, name)
    if block is None:
        raise KeyError("找不到形状 %s" % name)
    body = re.search(r"<p:txBody>.*?</p:txBody>", block, re.S).group(0)
    paras = re.findall(r"<a:p>.*?</a:p>", body, re.S)
    para = paras[0]
    run = re.search(r"<a:r>.*?</a:r>", para, re.S)
    rpr = ""
    if run:
        m = re.search(r"<a:rPr[^>]*/>|<a:rPr[^>]*>.*?</a:rPr>", run.group(0), re.S)
        if m:
            rpr = m.group(0)
    new_paras = "".join(
        "<a:p><a:r>%s<a:t>%s</a:t></a:r></a:p>" % (rpr, esc(t)) for t in lines if t is not None
    )
    open_tag = body[: body.index("<a:p>")] if "<a:p>" in body else body[: -len("</p:txBody>")]
    new_body = open_tag + new_paras + "</p:txBody>"
    return xml.replace(block, block.replace(body, new_body))


def move_shape(xml, name, x=None, y=None):
    """移动形状的 xfrm 偏移（厘米）。"""
    block = find_shape(xml, name)
    if block is None:
        raise KeyError("找不到形状 %s" % name)
    m = re.search(r'<a:off x="(-?\d+)" y="(-?\d+)"/>', block)
    nx = int(round(x * CM)) if x is not None else int(m.group(1))
    ny = int(round(y * CM)) if y is not None else int(m.group(2))
    new_block = block.replace(m.group(0), '<a:off x="%d" y="%d"/>' % (nx, ny), 1)
    return xml.replace(block, new_block)


def drop_shape(xml, name):
    block = find_shape(xml, name)
    return xml.replace(block, "") if block else xml


def build(src, out):
    zin = zipfile.ZipFile(src)
    parts = {n: zin.read(n) for n in zin.namelist()}
    zin.close()
    slide = lambda n: re.sub(r"^<\?xml[^>]*\?>\r?\n", "", parts["ppt/slides/slide%d.xml" % n].decode("utf-8"))

    # ---- 蓝图1 cover：只换占位说明文字，几何不动 ----
    cover = slide(1)
    cover = set_text(cover, "标题 1", ["论文中文题目（微软雅黑，44号，居中）"])
    cover = set_text(cover, "副标题 2", ["发表于 会议/期刊全称 年份（CCF-x/SCI x区）· 论文总结汇报（等线，24号）"])
    cover = set_text(cover, "文本框 3", ["汇报人：xxx（楷体，20号）", "单位：xxx（楷体，20号）", "xxxx年xx月xx日（楷体，20号）"])

    # ---- 蓝图2 text-left：原 slide5 左右镜像（图移右、正文移左），总结条不动 ----
    text_left = slide(5)
    # 图槽 18.16 宽，镜像后 x = 33.87 - 1.13 - 18.16 = 14.58
    text_left = move_shape(text_left, "矩形 2", x=round(CANVAS_W - 1.13 - 18.16, 2))
    text_left = move_shape(text_left, "文本框 31", x=1.13)
    text_left = set_text(text_left, "文本框 1", ["一.论文概述（或 二.研究背景 / 三.研究问题 / 七.总结）"])
    text_left = set_text(text_left, "文本框 31", [
        "标签：主句（楷体，18号，3~6条，条间有逻辑）",
        "标签下二级行（楷体，16号，最多3条）",
    ])
    text_left = set_text(text_left, "文本框 33", ["本页一句话结论（楷体，16号）。"])
    text_left = set_text(text_left, "矩形 2", ["图/表1：原文图优先（框架图/动机图/结果图），无则自绘"])

    # ---- 蓝图3 status：原 slide4 原样，只换说明文字 ----
    status = slide(4)
    status = set_text(status, "文本框 1", ["四.传统研究现状"])
    status = set_text(status, "文本框 31", [
        "总述：已有工作分几类、总体缺什么（楷体，18号）",
        "类别一：原理 → 不足（楷体，18号，须带原文引用）",
        "类别二：原理 → 不足（楷体，18号）",
    ])
    status = set_text(status, "矩形 2", ["图1：方法分类框架图（自绘 framework，类别+原理+不足）"])
    status = set_text(status, "矩形 8", ["表2：已有方法对比表（原文 Table 优先，或重排 table）"])

    # ---- 蓝图4 figure-left：原 slide5 原样 ----
    figure_left = slide(5)
    figure_left = set_text(figure_left, "文本框 1", ["五.本文研究方法：核心原理（或 …：步骤x / 六.实验结果：xxx）"])
    figure_left = set_text(figure_left, "文本框 31", [
        "标签：主句（楷体，18号，3~5条）",
        "标签下二级行（楷体，16号）",
    ])
    figure_left = set_text(figure_left, "文本框 33", ["本页一句话结论（楷体，16号）。"])
    figure_left = set_text(figure_left, "矩形 2", ["图/表1：原文框架图/机制图/结果图优先，无现成流程图则自绘 flow"])

    # ---- 蓝图5 closing：要点回顾 + 感谢聆听（居中）----
    closing = slide(14)
    closing = set_text(closing, "标题 1", ["要点回顾"])
    closing = set_text(closing, "文本框 6", ["一句话 Take-away：关键词 {{红粗}}（楷体，24号）", "出处：英文原题 · 会议/期刊 年份 / arXiv 编号（15号）"])
    closing = set_text(closing, "文本框 3", ["感谢聆听（居中，华文中宋36号）"])

    pages = [cover, text_left, status, figure_left, closing]
    src_rels = [1, 5, 4, 5, 14]  # 每张蓝图复制自的原页（沿用其版式/图片关系）

    # ---- 装配：5 张幻灯片 + 1 张备注模板，其余删除 ----
    keep = set(parts)
    for name in list(keep):
        if re.match(r"ppt/slides/slide\d+\.xml$", name) or re.match(
            r"ppt/slides/_rels/slide\d+\.xml\.rels$", name
        ):
            keep.discard(name)
        if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", name) or re.match(
            r"ppt/notesSlides/_rels/notesSlide\d+\.xml\.rels$", name
        ):
            keep.discard(name)
        if name.startswith("ppt/changesInfos/") or name == "docProps/thumbnail.jpeg":
            keep.discard(name)

    for i, xml in enumerate(pages, 1):
        parts["ppt/slides/slide%d.xml" % i] = (XML_DECL + xml).encode("utf-8")
        keep.add("ppt/slides/slide%d.xml" % i)
        # 幻灯片关系：沿用源页关系，去掉 notesSlide 指向（备注统一挂到 slide2 的模板上）
        rels = parts["ppt/slides/_rels/slide%d.xml.rels" % src_rels[i - 1]].decode("utf-8")
        items = [
            (rid, rtype, target)
            for rid, rtype, target in re.findall(
                r'<Relationship Id="([^"]+)" Type="[^"]*/(\w+)" Target="([^"]+)"', rels
            )
            if rtype != "notesSlide"
        ]
        if i == 2:  # 备注模板挂在 slide2 上，保证包结构有效
            items.append(("rIdNotes", "notesSlide", "../notesSlides/notesSlide1.xml"))
        parts["ppt/slides/_rels/slide%d.xml.rels" % i] = rels_doc(items).encode("utf-8")
        keep.add("ppt/slides/_rels/slide%d.xml.rels" % i)

    # 备注模板：取原模板任意一页备注，回指 slide2
    notes_tpl = next(
        (n for n in sorted(parts) if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n)), None
    )
    if notes_tpl:
        nx = re.sub(r"^<\?xml[^>]*\?>\r?\n", "", parts[notes_tpl].decode("utf-8"))
        nx = re.sub(r'Target="\.\./slides/slide\d+\.xml"', 'Target="../slides/slide2.xml"', nx)
        parts["ppt/notesSlides/notesSlide1.xml"] = (XML_DECL + nx).encode("utf-8")
        parts["ppt/notesSlides/_rels/notesSlide1.xml.rels"] = rels_doc(
            [("rId1", "notesMaster", "../notesMasters/notesMaster1.xml"),
             ("rId2", "slide", "../slides/slide2.xml")]
        ).encode("utf-8")
        keep.add("ppt/notesSlides/notesSlide1.xml")
        keep.add("ppt/notesSlides/_rels/notesSlide1.xml.rels")
    else:
        print("[template] 警告：原模板没有备注页，build_ppt 将无法写入旁白", file=sys.stderr)

    # presentation.xml：重建 sldIdLst
    pres = parts["ppt/presentation.xml"].decode("utf-8")
    pres_rels = [
        (rid, rtype, target)
        for rid, rtype, target in re.findall(
            r'<Relationship Id="([^"]+)" Type="[^"]*/(\w+)" Target="([^"]+)"',
            parts["ppt/_rels/presentation.xml.rels"].decode("utf-8"),
        )
        if rtype not in ("slide", "changesInfo", "thumbnail", "customXml")
    ]
    max_id = max([int(m.group(1)) for m in re.finditer(r'Id="rId(\d+)"', pres)] or [0])
    slide_rels = []
    for i in range(1, 6):
        rid = "rId%d" % (max_id + i)
        slide_rels.append((rid, "slide", "slides/slide%d.xml" % i))
    pres_rels.extend(slide_rels)
    sld_lst = "<p:sldIdLst>%s</p:sldIdLst>" % "".join(
        '<p:sldId id="%d" r:id="%s"/>' % (255 + i, rid) for i, (rid, _, _) in enumerate(slide_rels, 1)
    )
    pres = re.sub(r"<p:sldIdLst>.*?</p:sldIdLst>", sld_lst, pres, flags=re.S)
    parts["ppt/presentation.xml"] = pres.encode("utf-8")
    parts["ppt/_rels/presentation.xml.rels"] = rels_doc(pres_rels).encode("utf-8")

    root_rels = [
        (rid, rtype, target)
        for rid, rtype, target in re.findall(
            r'<Relationship Id="([^"]+)" Type="[^"]*/(\w+)" Target="([^"]+)"',
            parts["_rels/.rels"].decode("utf-8"),
        )
        if rtype != "thumbnail"
    ]
    parts["_rels/.rels"] = rels_doc(root_rels).encode("utf-8")

    for name in list(parts):
        if name not in keep and name != "[Content_Types].xml":
            parts.pop(name)
    parts["[Content_Types].xml"] = content_types_doc(parts).encode("utf-8")
    if "docProps/app.xml" in parts:
        parts["docProps/app.xml"] = app_doc(5).encode("utf-8")

    order = ["[Content_Types].xml"] + [n for n in sorted(parts) if n != "[Content_Types].xml"]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in order:
            zout.writestr(name, parts[name])
    print("[template] 完成：%s（5 张蓝图：cover/text-left/status/figure-left/closing）" % out)


def rels_doc(items):
    parts = [XML_DECL, '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for rid, rtype, target in items:
        parts.append('<Relationship Id="%s" Type="%s/%s" Target="%s"/>' % (rid, REL_BASE, rtype, target))
    parts.append("</Relationships>")
    return "".join(parts)


def content_types_doc(parts):
    types = [
        ("/ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"),
        ("/ppt/slideMasters/", "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"),
        ("/ppt/slideLayouts/", "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"),
        ("/ppt/notesMasters/", "application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml"),
        ("/ppt/notesSlides/", "application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"),
        ("/ppt/slides/", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"),
        ("/ppt/theme/", "application/vnd.openxmlformats-officedocument.theme+xml"),
        ("/ppt/presProps.xml", "application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"),
        ("/ppt/viewProps.xml", "application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"),
        ("/ppt/tableStyles.xml", "application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"),
        ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
        ("/docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml"),
        ("/docProps/custom.xml", "application/vnd.openxmlformats-officedocument.custom-properties+xml"),
    ]
    defaults = [("jpeg", "image/jpeg"), ("png", "image/png"), ("wdp", "image/vnd.ms-photo"),
                ("rels", "application/vnd.openxmlformats-package.relationships+xml"), ("xml", "application/xml")]
    out = [XML_DECL, '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">']
    for ext, ct in defaults:
        out.append('<Default Extension="%s" ContentType="%s"/>' % (ext, ct))
    for path in sorted(parts):
        if not path.endswith(".xml") or path == "[Content_Types].xml" or "/_rels/" in path:
            continue
        for prefix, ct in types:
            np = prefix.lstrip("/")
            if path == np or (np.endswith("/") and path.startswith(np)):
                out.append('<Override PartName="/%s" ContentType="%s"/>' % (path, ct))
                break
    out.append("</Types>")
    return "".join(out)


def app_doc(n):
    return (
        XML_DECL
        + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"'
        ' xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        "<Application>Microsoft Office PowerPoint</Application><PresentationFormat>宽屏</PresentationFormat>"
        "<Slides>%d</Slides><HiddenSlides>0</HiddenSlides><MMClips>0</MMClips><ScaleCrop>false</ScaleCrop>"
        "<LinksUpToDate>false</LinksUpToDate><SharedDoc>false</SharedDoc><HyperlinksChanged>false</HyperlinksChanged>"
        "<AppVersion>16.0000</AppVersion></Properties>" % n
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="/home/jgfeng/.claude/skills/proposalword2ppt/assets/template.pptx")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    import os
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "template.pptx")
    build(args.src, os.path.normpath(out))
