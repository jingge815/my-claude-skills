"""校验生成的申请书 .docx 是否忠实于 md 源，并符合 Word 渲染规则（结构性回归检查）。

用法: .venv/bin/python scripts/verify_docx.py <docx> <md> [--allow-prefix N] [--json report.json]
  --allow-prefix N  模板模式：docx 正文前有 N 个模板原有段落（含空段，= 构建器打印的「原有 N 个段落」），
                    其间的表格/图片一并跳过；默认 0
  --json PATH       另写 JSON 报告（findings / checks / 计数）

md 方言（与 build_docx.py 逐条一致）: # 标题 / ## ### #### #####（###### 按四级）节标题 / 　　正文 /
![](path) 插图 / 图N 题名 图题 / 表N 题名 + | a | b | 表格 / [n] 参考文献 / > 灰色说明段 /
``` 代码块（逐行成段）/ --- 分隔线忽略 / 1. - 条目按正文 / **加粗** *斜体* `代码`（标记剥离留文字）。
比较时去掉全部空白（含 U+3000）、统一引号“”→" 后逐块比对，标点保留。

检查项（E=错误必须修复，W=警告需人工确认）:
  V01 段落逐段一致(E)   md 与 docx 逐块（表格按单元格展开）数量、文本一致
  V02 图片数量(E)       图片段落数 == md 图片行数
  V03 图片行距(E)       图片段落不得 lineRule="exact"（Word 会把图裁掉——图"消失"的根因），w:line 非 auto 报警
  V04 图片尺寸(E)       宽 ≤ 版心（页宽-左右边距），高 ≤ 22cm；逐图报告 cm 尺寸
  V05 图片居中(W)       图片段落 w:jc=center
  V06 表格(E/W)         表数、表头、行数一致；表头缺底纹报警
  V07 参考文献(E)       [n] 段数 == md，编号从 1 连续
  V08 首行缩进(E)       正文段 w:ind firstLineChars=200 或 firstLine≥400
  V09 标题字体(W)       标题 16pt 粗、## 14pt 粗、###/####/##### 12pt 粗
  V10 pPr 顺序(E)       pPr 子元素符合 OOXML schema 顺序（乱序 Word 会报文件损坏）
  V11 引用首现顺序(E)   正文中 [n] 首次出现须为 1,2,3,…（与 md 一致）
  V12 eastAsia 字体(W)  正文 run 须设 w:rFonts@w:eastAsia
  V13 图片有效 DPI(W)   像素宽/显示宽 < 110dpi 报警
  V14 文档可打开(E)     zip 完整、python-docx 可加载且有段落

build/verify 分离：构建脚本只生成，本校验脚本作为每轮修订后的回归检查。
段落编号「第k段」= doc.paragraphs[k-1]（含空段，1 起）。退出码: 有错误 1，否则 0；文件不存在 2。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree
from PIL import Image

# ---- md 方言：正则与 build_docx.py 保持一致，保证期望块与构建器的切分完全相同 ----
INLINE_RE = re.compile(r"\*\*(.+?)\*\*|(?<![A-Za-z0-9*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)|`([^`\n]*)`")
IMG_RE = re.compile(r"^!\[[^\]]*\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)$")
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
REF_RE = re.compile(r"^\[(\d+)\]\s*\S")
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
CAP_RE = re.compile(r"^([图表])\s*(\d+)\s*(\S.{0,80})$")
HR_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
CITE_RE = re.compile(r"\[(\d+(?:\s*[,，]\s*\d+|\s*[-–]\s*\d+)*)\]")
EMU_PER_CM, EMU_PER_TWIP = 360000, 635
MAX_IMG_H_EMU = 22 * EMU_PER_CM
HEAD_PT = {"title": 16, "h1": 14, "h2": 12, "h3": 12, "h4": 12}   # 半点 = pt×2
HEAD_NAME = {"title": "文档标题", "h1": "## 节标题", "h2": "### 小节标题", "h3": "#### 标题", "h4": "##### 标题"}
KIND_NAME = {"p": "正文", "caption": "题注", "note": "说明", "code": "代码", "ref": "文献", "cell": "表格单元格"}
PPR_ORDER = ("keepNext keepLines pageBreakBefore framePr widowControl numPr suppressLineNumbers pBdr shd tabs "
             "suppressAutoHyphens kinsoku wordWrap overflowPunct topLinePunct autoSpaceDE autoSpaceDN bidi "
             "adjustRightInd snapToGrid spacing ind contextualSpacing mirrorIndents suppressOverlap jc "
             "textDirection textAlignment textboxTightWrap outlineLvl divId cnfStyle rPr sectPr pPrChange").split()
PPR_RANK = {qn("w:" + t): i for i, t in enumerate(PPR_ORDER)}
PPR_RANK[qn("w:keep")] = PPR_RANK[qn("w:keepNext")]      # 容错别名
PPR_RANK[qn("w:pStyle")] = -1                             # pStyle 必为首元素，不参与乱序判定
CHECK_NAMES = {
    "V01": "段落逐段一致", "V02": "图片数量", "V03": "图片行距", "V04": "图片尺寸", "V05": "图片居中",
    "V06": "表格", "V07": "参考文献", "V08": "首行缩进", "V09": "标题字体", "V10": "pPr 子元素顺序",
    "V11": "引用首现顺序", "V12": "字体 eastAsia", "V13": "图片有效 DPI", "V14": "文档可打开",
}


# ---------------------------------------------------------------- 结果收集
@dataclass
class Finding:
    eid: str
    level: str
    check: str
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checks: dict[str, dict] = field(default_factory=dict)
    _n: dict = field(default_factory=lambda: {"E": 0, "W": 0})

    def add(self, check: str, level: str, msg: str) -> None:
        self._n[level] += 1
        self.findings.append(Finding(f"{level}{self._n[level]:02d}", level, check, msg))

    def done(self, check: str, detail: str = "") -> None:
        self.checks[check] = {"id": check, "name": CHECK_NAMES[check], "detail": detail}

    def status(self, check: str) -> str:
        lv = {f.level for f in self.findings if f.check == check}
        return "error" if "E" in lv else "warn" if "W" in lv else "pass"

    @property
    def n_err(self) -> int:
        return sum(f.level == "E" for f in self.findings)

    @property
    def n_warn(self) -> int:
        return sum(f.level == "W" for f in self.findings)


def norm(s: str) -> str:
    """比较用归一化：去全部空白（含全角空格），统一引号，保留标点。"""
    s = (s or "").replace("　", "").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", "", s)


def q(s: str, n: int = 40) -> str:
    """证据摘录：空白折叠后截断 n 字。"""
    s = re.sub(r"\s+", " ", (s or "").replace("　", " ")).strip()
    return s[:n] + ("…" if len(s) > n else "")


def local(tag) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else str(tag)


# ---------------------------------------------------------------- md 解析 → 期望块
def strip_inline(s: str) -> str:
    """与构建器 add_rich 一致：**加粗** → 文字；*斜体*、`代码` → 剥离标记保留文字。"""
    return INLINE_RE.sub(lambda m: m.group(1) or m.group(2) or m.group(3) or "", s)


def split_row(row: str) -> list[str]:
    return [strip_inline(c) for c in row.strip().strip("|").split("|")]


def is_sep(s: str) -> bool:
    return bool(s) and set(s) <= set("|-: ") and "-" in s


def parse_md(md_path: Path) -> list[tuple[str, object]]:
    """与构建器同样的规则把 md 切成期望块序列: (kind, text|path|rows)，kind ∈
    title/h1..h4/p/img/caption/table/ref/note/code。列表条目按正文(p)处理，与构建器一致。"""
    lines = md_path.read_text(encoding="utf-8-sig").splitlines()
    blocks: list[tuple[str, object]] = []
    in_code, i = False, 0
    while i < len(lines):
        line = lines[i].rstrip()
        s = line.strip()
        if FENCE_RE.match(line):                                # 代码块围栏
            in_code = not in_code
        elif in_code:                                           # 代码块逐行成段（空行被构建器渲染为空段，忽略）
            if s:
                blocks.append(("code", line))
        elif not s or HR_RE.match(s):
            pass                                                # 空行 / 分隔线：构建器忽略
        elif m := HEAD_RE.match(s):
            lv = len(m.group(1))
            text = strip_inline(re.sub(r"\s+#+$", "", m.group(2)).strip())
            blocks.append(("title", text) if lv == 1 else (f"h{min(lv - 2, 3) + 1}", text))
        elif s.startswith(">"):
            blocks.append(("note", strip_inline(s[1:].strip())))
        elif m := IMG_RE.match(s):
            blocks.append(("img", m.group(1)))
        elif CAP_RE.match(s) and not line.startswith((" ", "\t", "　")) and not s.endswith(("。", "；", "，")):
            blocks.append(("caption", strip_inline(s)))
        elif s.startswith("|"):
            if i + 1 < len(lines) and is_sep(lines[i + 1].strip()):
                rows, k = [split_row(s)], i + 2
                while k < len(lines) and lines[k].strip().startswith("|"):
                    rows.append(split_row(lines[k]))
                    k += 1
                ncol = len(rows[0])
                blocks.append(("table", [rows[0]] + [(r + [""] * ncol)[:ncol] for r in rows[1:]]))
                i = k - 1
            else:
                blocks.append(("p", strip_inline(s)))          # 缺分隔行：构建器按正文渲染
        elif REF_RE.match(s):
            blocks.append(("ref", strip_inline(s)))
        else:
            blocks.append(("p", strip_inline(s)))
        i += 1
    return blocks


# ---------------------------------------------------------------- docx 提取
@dataclass
class Item:
    kind: str            # p / img / table / empty
    text: str | list     # 段落文本；table 时为 [[单元格文本]]
    el: object
    pidx: int = -1       # 正文段序号（1 起），表格为 -1


def para_text(p) -> str:
    return "".join(t.text or "" for t in p.iter(qn("w:t")))


def is_img_para(p) -> bool:
    return p.find(".//" + qn("w:drawing")) is not None or p.find(".//" + qn("pic:pic")) is not None


def body_items(doc) -> list[Item]:
    """按文档顺序提取正文块（递归进入 w:sdt），段落编号与 doc.paragraphs 一致。"""
    items: list[Item] = []
    pidx = 0

    def walk(container):
        nonlocal pidx
        for ch in container:
            if ch.tag == qn("w:p"):
                pidx += 1
                t = para_text(ch)
                kind = "img" if is_img_para(ch) else ("p" if norm(t) else "empty")
                items.append(Item(kind, t, ch, pidx))
            elif ch.tag == qn("w:tbl"):
                rows = [["".join(para_text(p) for p in tc.findall(qn("w:p"))) for tc in tr.findall(qn("w:tc"))]
                        for tr in ch.findall(qn("w:tr"))]
                items.append(Item("table", rows, ch))
            elif ch.tag == qn("w:sdt"):
                c = ch.find(qn("w:sdtContent"))
                if c is not None:
                    walk(c)

    walk(doc.element.body)
    return items


def apply_prefix(items: list[Item], n: int, rep: Report) -> list[Item]:
    """模板模式：跳过前 n 个正文段落（含空段，即构建器打印的「原有 n 个段落」）及其间的表格/图片，
    再跳过紧随其后的表格/空段，到第一个非空段落为止。"""
    if n <= 0:
        return items
    k, cnt = 0, 0
    while k < len(items) and cnt < n:
        cnt += items[k].kind != "table"
        k += 1
    while k < len(items) and items[k].kind in ("table", "empty"):
        k += 1
    if cnt < n:
        rep.add("V01", "W", f"--allow-prefix {n} 大于 docx 前置段落数 {cnt}，请核对该值")
    return items[k:]


def flatten_expected(blocks):
    out = []
    for kind, payload in blocks:
        if kind == "table":
            out.extend(("cell", c, "cell") for row in payload for c in row)
        elif kind == "img":
            out.append(("img", "", "img"))
        else:
            out.append(("text", payload, kind))
    return out


def flatten_docx(items):
    out = []
    for it in items:
        if it.kind == "table":
            out.extend(("cell", c, it) for row in it.text for c in row)
        elif it.kind == "img":
            out.append(("img", it.text, it))
        elif it.kind == "p":
            out.append(("text", it.text, it))
    return out


# ---------------------------------------------------------------- 样式继承（段落/ run 级）
class StyleLookup:
    """按 Word 继承链查找属性：自身 → 样式（沿 basedOn）→ docDefaults。"""

    def __init__(self, doc):
        st = doc.styles.element
        self.styles = st
        self.def_p = st.find(qn("w:docDefaults") + "/" + qn("w:pPrDefault") + "/" + qn("w:pPr"))
        self.def_r = st.find(qn("w:docDefaults") + "/" + qn("w:rPrDefault") + "/" + qn("w:rPr"))
        self.default_para = next((s.get(qn("w:styleId")) for s in st.findall(qn("w:style"))
                                  if s.get(qn("w:type")) == "paragraph" and s.get(qn("w:default")) in ("1", "true")), None)

    def _style(self, sid):
        return next((s for s in self.styles.findall(qn("w:style")) if s.get(qn("w:styleId")) == sid), None)

    def _chain(self, sid):
        seen = set()
        while sid and sid not in seen:
            seen.add(sid)
            st = self._style(sid)
            if st is None:
                break
            yield st
            b = st.find(qn("w:basedOn"))
            sid = b.get(qn("w:val")) if b is not None else None

    def para_style_id(self, p):
        ppr = p.find(qn("w:pPr"))
        ps = ppr.find(qn("w:pStyle")) if ppr is not None else None
        return ps.get(qn("w:val")) if ps is not None else self.default_para

    def ppr_chain(self, p):
        ppr = p.find(qn("w:pPr"))
        if ppr is not None:
            yield ppr
        for st in self._chain(self.para_style_id(p)):
            e = st.find(qn("w:pPr"))
            if e is not None:
                yield e
        if self.def_p is not None:
            yield self.def_p

    def rpr_chain(self, r, p):
        rpr = r.find(qn("w:rPr"))
        if rpr is not None:
            yield rpr
        rs = rpr.find(qn("w:rStyle")) if rpr is not None else None
        for st in self._chain(rs.get(qn("w:val")) if rs is not None else None):
            e = st.find(qn("w:rPr"))
            if e is not None:
                yield e
        for st in self._chain(self.para_style_id(p)):
            e = st.find(qn("w:rPr"))
            if e is not None:
                yield e
        if self.def_r is not None:
            yield self.def_r


def lookup(chain, tag: str):
    """继承链中第一个 tag 元素。"""
    return next((e.find(qn(tag)) for e in chain if e is not None and e.find(qn(tag)) is not None), None)


def lookup_attr(chain, tag: str, attr: str):
    """属性级继承：链中第一个带 attr 的 tag 元素的属性值。"""
    for e in chain:
        if e is None:
            continue
        t = e.find(qn(tag))
        if t is not None and t.get(qn(attr)) is not None:
            return t.get(qn(attr))
    return None


def text_runs(p):
    return [r for r in p.findall(qn("w:r")) if r.find(qn("w:t")) is not None]


# ---------------------------------------------------------------- 检查项
def check_v14(rep: Report, path: Path):
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
    except Exception as e:  # noqa: BLE001
        rep.add("V14", "E", f"zip 包无法打开: {e}")
        return None
    if bad:
        rep.add("V14", "E", f"zip 包校验失败（CRC 错误）: {bad}")
        return None
    try:
        doc = Document(str(path))
    except Exception as e:  # noqa: BLE001
        rep.add("V14", "E", f"python-docx 无法加载: {e}")
        return None
    if not doc.paragraphs:
        rep.add("V14", "E", "文档没有任何段落")
        return None
    rep.done("V14", f"zip 完整，{len(doc.paragraphs)} 段可加载")
    return doc


def check_v01(rep: Report, blocks, items, n_skip):
    exp, got = flatten_expected(blocks), flatten_docx(items)
    rep.done("V01", f"md {len(blocks)} 块 / docx {len([i for i in items if i.kind != 'empty'])} 块（跳过前缀 {n_skip}）")
    if len(exp) != len(got):
        n_dx = len([i for i in items if i.kind != "empty"])
        rep.add("V01", "E", f"块数不一致: md {len(exp)} / docx {len(got)}（表格按单元格展开计；按块计 md {len(blocks)} / docx {n_dx}）")
    for i, (e, g) in enumerate(zip(exp, got)):
        if e[0] != g[0] or norm(e[1]) != norm(g[1]):
            where = f"第{g[2].pidx}段" if g[0] != "cell" else "表格内"
            rep.add("V01", "E", f"第 {i + 1} 块（{KIND_NAME.get(e[2], e[2])}）不一致，期望「{q(e[1])}」实得「{q(g[1])}」（{where}）")
            break
    n = min(len(exp), len(got))
    return [(exp[i][2], exp[i][1], got[i][2]) for i in range(n) if exp[i][0] == got[i][0]]


def check_v02(rep: Report, blocks, items):
    n_md = sum(k == "img" for k, _ in blocks)
    imgs = [it for it in items if it.kind == "img"]
    rep.done("V02", f"md {n_md} 张 / docx {len(imgs)} 段")
    if len(imgs) != n_md:
        rep.add("V02", "E", f"图片数量不一致: md {n_md} 行 / docx {len(imgs)} 个图片段落")
    return imgs


def check_v03(rep: Report, imgs, sl: StyleLookup):
    for it in imgs:
        chain = list(sl.ppr_chain(it.el))
        line = lookup_attr(chain, "w:spacing", "w:line")
        rule = lookup_attr(chain, "w:spacing", "w:lineRule")
        direct = it.el.find(qn("w:pPr") + "/" + qn("w:spacing")) is not None
        src = "" if direct else "（继承自样式）"
        if rule == "exact":
            rep.add("V03", "E", f'第{it.pidx}段（图片）行距 lineRule="exact" w:line="{line}"，Word 会把图裁成一行高——图"消失"的根因{src}；删掉该 w:line 或改 auto')
        elif line is not None and rule != "auto":
            rep.add("V03", "W", f'第{it.pidx}段（图片）行距 w:line="{line}" lineRule="{rule or "未声明"}"{src}，建议删除 w:line 或显式 auto')
    rep.done("V03", f"{len(imgs)} 个图片段落")


def text_width_emu(body) -> int:
    sect = body.find(qn("w:sectPr"))
    if sect is None:
        sect = body.find(".//" + qn("w:sectPr"))
    pg, mar = (sect.find(qn("w:pgSz")), sect.find(qn("w:pgMar"))) if sect is not None else (None, None)
    tw = lambda e, a, d: int(e.get(qn(a))) if e is not None and e.get(qn(a)) else d  # noqa: E731
    return (tw(pg, "w:w", 11906) - tw(mar, "w:left", 1417) - tw(mar, "w:right", 1417)) * EMU_PER_TWIP


def check_v04(rep: Report, imgs, body):
    width = text_width_emu(body)
    sizes = []
    for k, it in enumerate(imgs, 1):
        for ext in it.el.iter(qn("wp:extent")):
            cx, cy = int(ext.get("cx")), int(ext.get("cy"))
            sizes.append(f"图{k} {cx / EMU_PER_CM:.2f}×{cy / EMU_PER_CM:.2f}cm")
            if cx > width:
                rep.add("V04", "E", f"第{it.pidx}段图{k} 宽 {cx / EMU_PER_CM:.2f}cm 超过版心宽 {width / EMU_PER_CM:.2f}cm")
            if cy > MAX_IMG_H_EMU:
                rep.add("V04", "E", f"第{it.pidx}段图{k} 高 {cy / EMU_PER_CM:.2f}cm 超过 22cm 上限")
    rep.done("V04", f"版心 {width / EMU_PER_CM:.2f}cm; " + "; ".join(sizes))


def check_v05(rep: Report, imgs, sl: StyleLookup):
    for k, it in enumerate(imgs, 1):
        jc = lookup(sl.ppr_chain(it.el), "w:jc")
        if jc is None or jc.get(qn("w:val")) != "center":
            rep.add("V05", "W", f"第{it.pidx}段图{k} 未设置居中（w:jc=center），当前 {jc.get(qn('w:val')) if jc is not None else '未设'}")
    rep.done("V05", f"{len(imgs)} 个图片段落")


def check_v06(rep: Report, blocks, items):
    md_tabs = [p for k, p in blocks if k == "table"]
    dx_tabs = [it for it in items if it.kind == "table"]
    rep.done("V06", f"md {len(md_tabs)} 个 / docx {len(dx_tabs)} 个")
    if len(md_tabs) != len(dx_tabs):
        rep.add("V06", "E", f"表格数量不一致: md {len(md_tabs)} 个 / docx {len(dx_tabs)} 个")
    for t, (md, it) in enumerate(zip(md_tabs, dx_tabs), 1):
        rows = it.text
        if not rows:
            rep.add("V06", "E", f"表{t} 在 docx 中没有任何行")
            continue
        if len(rows[0]) != len(md[0]):
            rep.add("V06", "E", f"表{t} 表头列数 md {len(md[0])} / docx {len(rows[0])}")
        for c, (a, b) in enumerate(zip(md[0], rows[0]), 1):
            if norm(a) != norm(b):
                rep.add("V06", "E", f"表{t} 表头第{c}列不一致，期望「{q(a)}」实得「{q(b)}」")
        if len(rows) != len(md):
            rep.add("V06", "E", f"表{t} 行数（含表头）md {len(md)} / docx {len(rows)}")
        for r, (mrow, drow) in enumerate(zip(md[1:], rows[1:]), 2):
            for c, (a, b) in enumerate(zip(mrow, drow), 1):
                if norm(a) != norm(b):
                    rep.add("V06", "E", f"表{t} 第{r}行第{c}列不一致，期望「{q(a)}」实得「{q(b)}」")
        tcs = it.el.findall(qn("w:tr"))[0].findall(qn("w:tc"))
        if not any(tc.find(qn("w:tcPr") + "/" + qn("w:shd")) is not None for tc in tcs):
            rep.add("V06", "W", f"表{t} 表头单元格无底纹（w:shd），建议浅灰底以区分表头")


def check_v07(rep: Report, blocks, items):
    refs = [it for it in items if it.kind == "p" and REF_RE.match(it.text.strip())]
    n_md = sum(k == "ref" for k, _ in blocks)
    rep.done("V07", f"md {n_md} 条 / docx {len(refs)} 段")
    if len(refs) != n_md:
        rep.add("V07", "E", f"参考文献条数不一致: md {n_md} 条 / docx {len(refs)} 段（形如 [n] 开头）")
    nums = [int(REF_RE.match(it.text.strip()).group(1)) for it in refs]
    if nums != list(range(1, len(nums) + 1)):
        rep.add("V07", "E", f"参考文献编号应从 1 连续递增，实际 {nums[:12]}{'…' if len(nums) > 12 else ''}")


def check_v08(rep: Report, aligned, sl: StyleLookup):
    n = 0
    for ekind, _, it in aligned:
        if ekind != "p":
            continue
        n += 1
        chain = list(sl.ppr_chain(it.el))
        chars = lookup_attr(chain, "w:ind", "w:firstLineChars")
        first = lookup_attr(chain, "w:ind", "w:firstLine")
        ok = (chars is not None and int(chars) >= 200) or (first is not None and int(first) >= 400)
        if not ok:
            rep.add("V08", "E", f'第{it.pidx}段正文缺首行缩进 2 字符（firstLineChars={chars or "未设"}, firstLine={first or "未设"}）: 「{q(it.text)}」')
    rep.done("V08", f"{n} 个正文段")


def check_v09(rep: Report, aligned, sl: StyleLookup):
    n = 0
    for ekind, _, it in aligned:
        if ekind not in HEAD_PT:
            continue
        n += 1
        runs = text_runs(it.el)
        if not runs:
            rep.add("V09", "W", f"第{it.pidx}段（{HEAD_NAME[ekind]}）没有文本 run: 「{q(it.text)}」")
            continue
        chain = list(sl.rpr_chain(runs[0], it.el))
        sz = lookup_attr(chain, "w:sz", "w:val")
        b = lookup(chain, "w:b")
        bold = b is not None and b.get(qn("w:val"), "1") not in ("0", "false", "off")
        probs = [] if bold else ["未加粗"]
        want = HEAD_PT[ekind]
        if sz is None or int(sz) != want * 2:
            probs.append(f"字号 {int(sz) / 2 if sz else '未设'}pt≠{want}pt")
        if probs:
            rep.add("V09", "W", f"第{it.pidx}段（{HEAD_NAME[ekind]}）{'、'.join(probs)}: 「{q(it.text)}」")
    rep.done("V09", f"{n} 个标题段")


def check_v10(rep: Report, items, body):
    tree, labels = body.getroottree(), {}
    for it in items:
        if it.pidx > 0:
            labels[tree.getpath(it.el)] = f"第{it.pidx}段"
    for t, it in enumerate([i for i in items if i.kind == "table"], 1):
        for r, tr in enumerate(it.el.findall(qn("w:tr")), 1):
            for c, tc in enumerate(tr.findall(qn("w:tc")), 1):
                for p in tc.findall(qn("w:p")):
                    labels[tree.getpath(p)] = f"表{t}第{r}行第{c}列"
    n = 0
    for ppr in body.iter(qn("w:pPr")):
        n += 1
        par = ppr.getparent()
        where = labels.get(tree.getpath(par)) if par.tag == qn("w:p") else f"{local(par.tag)}/pPr"
        last_tag, last_rank = None, -1
        for ch in ppr:
            rank = PPR_RANK.get(ch.tag)
            if rank is None:
                continue
            if rank < last_rank:
                rep.add("V10", "E", f"{where} 的 pPr 子元素乱序: w:{last_tag} 排在了 w:{local(ch.tag)} 之前"
                                    f"（schema 要求 {' → '.join(PPR_ORDER[rank:last_rank + 1])}），Word 可能报文件损坏")
                break
            last_tag, last_rank = local(ch.tag), rank
    rep.done("V10", f"{n} 个 pPr")


def expand_cites(group: str) -> list[int]:
    out = []
    for part in re.split(r"[,，]", group):
        part = part.strip()
        if m := re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part):
            out.extend(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def cite_firsts(text: str) -> list[int]:
    seq = []
    for m in CITE_RE.finditer(text):
        for n in expand_cites(m.group(1)):
            if n < 1000 and n not in seq:
                seq.append(n)
    return seq


def first_break(seq):
    return next(((i, n) for i, n in enumerate(seq, 1) if n != i), None)


def check_v11(rep: Report, blocks, items):
    md_text = "\n".join(str(p) for k, p in blocks if k != "ref")
    dx_text = "\n".join(it.text if it.kind != "table" else "\n".join(c for r in it.text for c in r)
                        for it in items if not (it.kind == "p" and REF_RE.match(it.text.strip())))
    dseq, mseq = cite_firsts(dx_text), cite_firsts(md_text)
    rep.done("V11", f"首现序列 {dseq[:12]}{'…' if len(dseq) > 12 else ''}")
    bad = first_break(dseq)
    if bad:
        note = "md 源顺序正确，疑为 docx 手工编辑后段落复制/粘贴错位" if first_break(mseq) is None \
            else f"md 源首现序列 {mseq[:12]} 同样不正确，请先修 md"
        rep.add("V11", "E", f"正文引用首现顺序应为 1,2,3,…，第 {bad[0]} 个首现的是 [{bad[1]}]（docx 首现序列 {dseq[:12]}）；{note}")


def check_v12(rep: Report, aligned, sl: StyleLookup):
    missing = []
    for ekind, _, it in aligned:
        if ekind != "p":
            continue
        for r in text_runs(it.el):
            if lookup_attr(sl.rpr_chain(r, it.el), "w:rFonts", "w:eastAsia") is None:
                missing.append((it.pidx, "".join(t.text or "" for t in r.iter(qn("w:t")))))
    if missing:
        eg = "、".join(f"第{p}段「{q(t, 12)}」" for p, t in missing[:3])
        rep.add("V12", "W", f"{len(missing)} 个正文 run 缺 w:rFonts@w:eastAsia（中文字体未指定，Word 会回退默认字体），如 {eg}")
    rep.done("V12", f"缺 eastAsia 的 run {len(missing)} 个")


def check_v13(rep: Report, imgs, doc):
    info = []
    for k, it in enumerate(imgs, 1):
        for ext in it.el.iter(qn("wp:extent")):
            blip = ext.getparent().find(".//" + qn("a:blip"))
            rid = blip.get(qn("r:embed")) if blip is not None else None
            if not rid:
                continue
            try:
                blob = doc.part.rels[rid].target_part.blob
                pw, ph = Image.open(io.BytesIO(blob)).size
            except Exception:  # noqa: BLE001
                continue
            w_cm, h_cm = int(ext.get("cx")) / EMU_PER_CM, int(ext.get("cy")) / EMU_PER_CM
            dpi = min(pw / (w_cm / 2.54), ph / (h_cm / 2.54)) if w_cm and h_cm else 0
            info.append(f"图{k} {dpi:.0f}dpi")
            if dpi < 110:
                rep.add("V13", "W", f"图{k}（第{it.pidx}段）有效 DPI {dpi:.0f}<110（像素 {pw}×{ph}，显示 {w_cm:.2f}×{h_cm:.2f}cm），印刷会模糊")
    rep.done("V13", "; ".join(info) if info else "无可读媒体")


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("docx")
    ap.add_argument("md")
    ap.add_argument("--allow-prefix", type=int, default=0, help="模板原有非空段落数，默认 0")
    ap.add_argument("--json", default=None, help="JSON 报告输出路径")
    args = ap.parse_args()
    docx_path, md_path = Path(args.docx), Path(args.md)
    for p in (docx_path, md_path):
        if not p.exists():
            print(f"[verify] 错误: 找不到 {p}", file=sys.stderr)
            return 2
    rep = Report()
    doc = check_v14(rep, docx_path)
    if doc is None:
        rep.done("V14", "文档不可用，其余检查跳过")
    else:
        blocks = parse_md(md_path)
        items = apply_prefix(body_items(doc), args.allow_prefix, rep)
        sl = StyleLookup(doc)
        body = doc.element.body
        aligned = check_v01(rep, blocks, items, args.allow_prefix)
        imgs = check_v02(rep, blocks, items)
        check_v03(rep, imgs, sl)
        check_v04(rep, imgs, body)
        check_v05(rep, imgs, sl)
        check_v06(rep, blocks, items)
        check_v07(rep, blocks, items)
        check_v08(rep, aligned, sl)
        check_v09(rep, aligned, sl)
        check_v10(rep, items, body)
        check_v11(rep, blocks, items)
        check_v12(rep, aligned, sl)
        check_v13(rep, imgs, doc)
    for f in rep.findings:
        print(f"[verify] {f.eid}: [{f.check} {CHECK_NAMES[f.check]}] {f.message}")
    for cid in sorted(rep.checks):
        c = rep.checks[cid]
        st = rep.status(cid)
        print(f"[verify] {cid} {c['name']}: {'通过' if st == 'pass' else ('错误' if st == 'error' else '警告')}"
              + (f"（{c['detail']}）" if c["detail"] else ""))
    n_checks = len(rep.checks)
    print(f"[verify] 结果: 错误 {rep.n_err} / 警告 {rep.n_warn} / 检查项 {n_checks}")
    if args.json:
        out = {"docx": str(docx_path), "md": str(md_path), "allow_prefix": args.allow_prefix,
               "errors": rep.n_err, "warnings": rep.n_warn, "checks_run": n_checks,
               "findings": [{"id": f.eid, "check": f.check, "level": f.level, "message": f.message} for f in rep.findings],
               "checks": [{**c, "status": rep.status(cid)} for cid, c in sorted(rep.checks.items())]}
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if rep.n_err else 0


if __name__ == "__main__":
    sys.exit(main())
