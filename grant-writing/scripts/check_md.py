#!/usr/bin/env python3
"""check_md.py — 申请书 markdown 机械校验（阶段 6「先机械后人工」的机械半场）。

用法：
    check_md.py <申请书.md> [--figs-dir <dir>] [--json <report.json>] [--quiet]
                [--max-body <n>] [--allow C03,C04,C18]
    --figs-dir   图片相对路径的备选根目录（默认：md 所在目录；先按 md 目录解析，再按 figs-dir）
    --json       把全部发现（check/level/line/message/evidence）与汇总计数写到该文件
    --quiet      不打印「通过」的检查项与 I 级提示行
    --max-body   论证正文汉字上限（C09），超出记 W
    --allow      列出的检查项内的错误照常打印，但不计入退出码（阶段 5b 门禁用：C03,C04,C18）

输出：每条发现一行 ``[check] E<nn> L<line>: <message>``（E 错误 / W 警告 / I 提示），
按检查项分组；末行 ``[check] 结果: 错误 N / 警告 M / 检查项 K``；退出码 1 当且仅当存在错误。

检查项与规范对应（「验证点 N」= references/stage-6-verify.md 九条验证点；「通则 N」= references/writing-rules.md 全文通则；「原则 N」= SKILL.md 不可违背的原则）：
    C01 禁词                验证点 2 重要性靠论述（禁自评断言）；通则 3 无出处起句
    C02 政策直引计数         验证点 2（政策原文引文全篇不超过两处）；原则 6
    C03 ASCII 框图           原则 12 图是画出来的；验证点 7
    C04 图片存在与编号       验证点 8（每张图有编号与名称，全文连续编号）
    C05 表格编号             验证点 8
    C06 先文后图             验证点 8（正文先引称「如图N所示」再出现图表；引称不得指向不存在的编号）
    C07 段首缩进             验证点 9；通则 2
    C08 标题层级体例         通则 1（一、 → （一） → 1． → 1.1）
    C09 字数双口径           通则 6（汉字按字符计，不含标题与图注；论证正文 / 全文双口径）
    C10 引用首现顺序         通则 7；阶段 7「每轮重跑机械校验」的引用首现顺序与参考文献一致性
    C11 口径一致             原则 7 口径唯一（论文篇数 / 原型套数 / 报告份数）
    C12 分类命名一致         验证点 3 现状分类有逻辑（分类维度与量词一致）
    C13 研究内容时态         原则 1 研究性重构；阶段 5 写作规范的时态要求（研究内容写拟开展）
    C14 风险措辞             原则 10 风险写成可控可克服的
    C15 周期段结构           writing-sections 研究周期（总起思路 + 分阶段）
    C16 研究目标结构         通则 4 先总后分；writing-sections 研究目标
    C17 术语一致             阶段 7 术语审查（同一概念全文用词唯一）
    C18 图片分辨率           验证点 7；figures.md（版心 16cm，宽 ≥1400px）
    C19 段落长度             通则 6 / writing-rules（单段 ≤400 字，450 为硬上限）
    C20 摘要位置             默认章节顺序（摘要单列，放在正文之前）；通则 1

本脚本只做可脚本化的「机械校验」；九条验证点的内容级审查与文字校对由审查员 / 校对员子 agent 按
references/stage-6-verify.md 执行，主 agent 只看本脚本的退出码与子 agent 的落盘记录。
行号均为 md 文件的 1-based 行号。纯标准库；C18 在有 Pillow 时才检查像素宽度。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

CHECKS: list[tuple[str, str]] = [
    ("C01", "禁词"), ("C02", "政策直引计数"), ("C03", "ASCII 框图"), ("C04", "图片存在与编号"),
    ("C05", "表格编号"), ("C06", "先文后图"), ("C07", "段首缩进"), ("C08", "标题层级体例"),
    ("C09", "字数双口径"), ("C10", "引用首现顺序"), ("C11", "口径一致"), ("C12", "分类命名一致"),
    ("C13", "研究内容时态"), ("C14", "风险措辞"), ("C15", "周期段结构"), ("C16", "研究目标结构"),
    ("C17", "术语一致"), ("C18", "图片分辨率"), ("C19", "段落长度"), ("C20", "摘要位置"),
]
CHECK_TITLE = dict(CHECKS)

INDENT = "　　"
CJK_RE = re.compile(r"[一-鿿]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
FIG_CAP_RE = re.compile(r"^图\s*(\d+)\s+(\S.*)$")
TAB_CAP_RE = re.compile(r"^表\s*(\d+)\s+(\S.*)$")
REF_LINE_RE = re.compile(r"^\[(\d+)\]\s+\S")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.．、)])\s+")
QUOTE_RE = re.compile(r"^\s*>")
MAX_SHOW = 10


def clip(text: str, n: int = 30) -> str:
    """截取片段用于报错引用。"""
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def cjk_count(text: str) -> int:
    return len(CJK_RE.findall(text))


@dataclass
class Finding:
    check: str
    level: str  # 'E' | 'W' | 'I'
    line: int | None
    message: str
    evidence: str = ""


@dataclass
class Image:
    path: str
    line: int          # 1-based 行号
    col: int           # 0-based 列
    resolved: Path | None
    caption_line: int | None = None
    number: int | None = None


@dataclass
class Ctx:
    lines: list[str]
    md_dir: Path
    figs_dir: Path
    kinds: list[str]                      # 每行的类别（见 classify）
    in_code: list[bool]                   # 代码围栏内部（含围栏行）
    in_ref: list[bool]                    # 参考文献节内部（不含节标题行）
    ref_heading: int | None = None        # 参考文献节标题行索引（0-based）
    abs_heading: int | None = None        # 摘要节标题行索引
    headings: list[tuple[int, int, str]] = field(default_factory=list)  # (idx, level, text)
    images: list[Image] = field(default_factory=list)
    body_indices: list[int] = field(default_factory=list)       # 正文文本行（含标题/列表，不含代码/引言/参考文献）
    para_indices: list[int] = field(default_factory=list)       # 正文段落行
    fig_caption_indices: list[int] = field(default_factory=list)  # 未挂在图片后的图注行
    tab_caption_indices: list[int] = field(default_factory=list)  # 表题行
    pil: bool = False
    max_body: int | None = None
    word_count: dict[str, int] | None = None

    def section(self, keyword: str) -> tuple[int, int] | None:
        """`## ` 标题含 keyword 的节：返回 (首个正文行索引, 结束索引(不含))，0-based。"""
        for idx, lvl, text in self.headings:
            if lvl == 2 and keyword in text:
                end = next((j for j, l2, _ in self.headings if j > idx and l2 == 2), len(self.lines))
                return idx + 1, end
        return None

    def paras_in(self, start: int, end: int) -> list[int]:
        return [i for i in self.para_indices if start <= i < end]

    def subheading_after(self, start: int, end: int, keyword: str) -> int | None:
        """节内第一个标题文本含 keyword 的子标题索引。"""
        for idx, lvl, text in self.headings:
            if start <= idx < end and keyword in text:
                return idx
        return None


def classify(line: str) -> str:
    """代码围栏之外的行类别；围栏/代码行由 build_ctx 直接标为 fence/code。"""
    if not line.strip():
        return "blank"
    if HEADING_RE.match(line):
        return "heading"
    s = line.lstrip()
    if s.startswith("!["):
        return "image"
    if FIG_CAP_RE.match(line):
        return "figcap"
    if TAB_CAP_RE.match(line):
        return "tabcap"
    if TABLE_ROW_RE.match(line):
        return "table"
    if QUOTE_RE.match(line):
        return "quote"
    if REF_LINE_RE.match(s):
        return "ref"
    if LIST_RE.match(line):
        return "list"
    return "para"


def build_ctx(lines: list[str], md_dir: Path, figs_dir: Path, pil: bool, max_body: int | None) -> Ctx:
    kinds: list[str] = []
    in_code_flags: list[bool] = []
    in_code = False
    fence_char = ""
    for line in lines:
        m = FENCE_RE.match(line)
        if not in_code and m:
            in_code, fence_char = True, m.group(1)[0]
            kinds.append("fence")
            in_code_flags.append(True)
            continue
        if in_code:
            in_code_flags.append(True)
            if m and m.group(1)[0] == fence_char:
                in_code = False
                kinds.append("fence")
            else:
                kinds.append("code")
            continue
        in_code_flags.append(False)
        kinds.append(classify(line))
    ctx = Ctx(lines=lines, md_dir=md_dir, figs_dir=figs_dir, kinds=kinds, in_code=in_code_flags,
              in_ref=[False] * len(lines), pil=pil, max_body=max_body)
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m and not in_code_flags[i]:
            ctx.headings.append((i, len(m.group(1)), m.group(2).strip()))
    for idx, lvl, text in ctx.headings:
        if lvl == 2 and text.startswith("参考文献") and ctx.ref_heading is None:
            ctx.ref_heading = idx
        if lvl == 2 and text.startswith("摘要") and ctx.abs_heading is None:
            ctx.abs_heading = idx
    if ctx.ref_heading is not None:
        for i in range(ctx.ref_heading + 1, len(lines)):
            ctx.in_ref[i] = True
    # 图片：非代码行内所有 ![]()
    for i, line in enumerate(lines):
        if in_code_flags[i]:
            continue
        for m in IMAGE_RE.finditer(line):
            rel = m.group(1)
            candidates = [md_dir / rel, figs_dir / rel, figs_dir / Path(rel).name]
            if Path(rel).is_absolute():
                candidates = [Path(rel)]
            resolved = next((c for c in candidates if c.is_file()), None)
            ctx.images.append(Image(path=rel, line=i + 1, col=m.start(), resolved=resolved))
    # 图注：图片后的下一个非空行
    occupied_caps: set[int] = set()
    for img in ctx.images:
        j = img.line  # 0-based 下一行
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and kinds[j] == "figcap":
            img.caption_line = j + 1
            img.number = int(FIG_CAP_RE.match(lines[j]).group(1))
            occupied_caps.add(j)
    ctx.body_indices = [i for i, k in enumerate(kinds) if k not in ("blank", "code", "fence", "quote", "ref") and not ctx.in_ref[i]]
    ctx.para_indices = [i for i, k in enumerate(kinds) if k == "para"]
    ctx.fig_caption_indices = [i for i, k in enumerate(kinds) if k == "figcap" and i not in occupied_caps]
    ctx.tab_caption_indices = [i for i, k in enumerate(kinds) if k == "tabcap"]
    return ctx


def snippets(text: str, start: int, width: int = 30) -> str:
    """以 start 为中心截取上下文片段。"""
    s = text[max(0, start - 8): start + width]
    return clip(s, width)


def collapse(check: str, hits: list[Finding]) -> list[Finding]:
    """最多展示 MAX_SHOW 条，其余合并成一条『…另有 N 处』。"""
    if len(hits) <= MAX_SHOW:
        return hits
    hidden = hits[MAX_SHOW:]
    level = "E" if any(f.level == "E" for f in hidden) else "W"
    return hits[:MAX_SHOW] + [Finding(check, level, None, f"…另有 {len(hidden)} 处同类问题")]


# ---------------------------------------------------------------- C01 禁词
BANNED_WORDS = ["非常重要", "十分重要", "极其重要", "意义重大", "具有重要价值", "具有重要意义",
                "具有重大意义", "众所周知", "随着技术的发展"]
BANNED_RE = re.compile("|".join(map(re.escape, BANNED_WORDS)) + r"|随着[^，。；！？\n]{0,12}的发展")
_QUOTE_PAIRS = {"「": "」", "『": "』", "《": "》", "“": "”", "‘": "’"}


def quoted_spans(line: str) -> list[tuple[int, int]]:
    """行内成对引号（「」『』《》“” 与 ASCII "）覆盖的区间。"""
    spans: list[tuple[int, int]] = []
    stack: list[tuple[str, int]] = []
    dq: int | None = None
    for i, ch in enumerate(line):
        if ch in _QUOTE_PAIRS:
            stack.append((ch, i))
        elif stack and ch == _QUOTE_PAIRS[stack[-1][0]]:
            spans.append((stack.pop()[1], i + 1))
        elif ch == '"':
            if dq is None:
                dq = i
            else:
                spans.append((dq, i + 1))
                dq = None
    return spans


def check_c01(ctx: Ctx) -> list[Finding]:
    hits: list[Finding] = []
    for i in ctx.body_indices:
        line = ctx.lines[i]
        spans = quoted_spans(line)
        for m in BANNED_RE.finditer(line):
            word = clip(m.group(0))
            quoted = any(s <= m.start() < e for s, e in spans)
            if quoted:
                hits.append(Finding("C01", "W", i + 1, f"禁词『{word}』位于引号内，若为政策原文引用可放行；否则改为数字与因果句或删",
                                    snippets(line, m.start())))
            else:
                hits.append(Finding("C01", "E", i + 1, f"禁词『{word}』属自评断言或无出处起句，改为数字与因果句或删（验证点 2）",
                                    snippets(line, m.start())))
    return collapse("C01", hits)


# ---------------------------------------------------------------- C02 政策直引计数
POLICY_RE = re.compile(r"《[^》]{2,40}》[^。；]{0,30}?(要求|提出|强调|指出|明确)")


def check_c02(ctx: Ctx) -> list[Finding]:
    hits: list[tuple[int, str]] = []
    for i in ctx.body_indices:
        for m in POLICY_RE.finditer(ctx.lines[i]):
            hits.append((i + 1, m.group(0)))
    out = [Finding("C02", "I", None, f"政策直接引用 {len(hits)} 处（上限 2 处）")]
    if len(hits) > 2:
        locs = "、".join(f"L{l}" for l, _ in hits)
        out.append(Finding("C02", "W", hits[2][0], f"政策直接引用 {len(hits)} 处超过两处上限（{locs}）；政策只在问题收束处作一句佐证",
                           clip(hits[2][1])))
    return out


# ---------------------------------------------------------------- C03 ASCII 框图
BOX_CHARS = set("┌┐└┘├┤│─┬┴┼╔╗╚╝╠╣╦╩╬║═→←↑↓▼▲►◄")


def code_blocks(ctx: Ctx) -> list[tuple[int, int]]:
    """成对的代码围栏 (起始行, 结束行)，0-based 含两端；未闭合的围栏延伸到文末。"""
    fences = [i for i, k in enumerate(ctx.kinds) if k == "fence"]
    return [(fences[a], fences[a + 1] if a + 1 < len(fences) else len(ctx.lines) - 1)
            for a in range(0, len(fences), 2)]


def check_c03(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    for s, e in code_blocks(ctx):
        bad = next((i for i in range(s + 1, e) if any(ch in BOX_CHARS for ch in ctx.lines[i])), None)
        if bad is not None:
            out.append(Finding("C03", "E", s + 1, "代码块内含 ASCII 框图，最终稿必须改为 ![](figs/figN.png) 插图（原则 12）",
                               f"L{bad + 1}: {clip(ctx.lines[bad])}"))
    return out


# ---------------------------------------------------------------- C04 图片存在与编号
def check_c04(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    if not ctx.images:
        return [Finding("C04", "I", None, "未发现 ![]() 插图；验证点 7 要求至少 1 张总体研究框架图（ASCII 框图见 C03）")]
    numbered: list[Image] = []
    for img in ctx.images:
        if img.resolved is None:
            out.append(Finding("C04", "E", img.line, f"图文件不存在：{img.path}（按 md 目录与 --figs-dir 均未找到）"))
        if img.caption_line is None:
            out.append(Finding("C04", "E", img.line, "图后缺图注行『图N 〈题名〉』（图片的下一个非空行须以『图N 』起头，不缩进）"))
        else:
            numbered.append(img)
            m = re.search(r"fig(\d+)", Path(img.path).stem, re.I)
            if m and int(m.group(1)) != img.number:
                out.append(Finding("C04", "W", img.caption_line,
                                   f"图注编号 {img.number} 与文件名 {Path(img.path).name} 不一致"))
    nums = [img.number for img in numbered]
    if nums != list(range(1, len(nums) + 1)):
        out.append(Finding("C04", "E", None, f"图编号不连续、重复或乱序：实际 {nums}，应为 {list(range(1, len(nums) + 1))}（验证点 8）"))
    for i in ctx.fig_caption_indices:
        out.append(Finding("C04", "E", i + 1, f"图注行『{clip(ctx.lines[i])}』前无 ![]() 图片行"))
    return out


# ---------------------------------------------------------------- C05 表格编号
def check_c05(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    caps: dict[int, list[int]] = {}
    for i in ctx.tab_caption_indices:
        n = int(TAB_CAP_RE.match(ctx.lines[i]).group(1))
        j = i + 1
        while j < len(ctx.lines) and not ctx.lines[j].strip():
            j += 1
        if not (j < len(ctx.lines) and ctx.kinds[j] == "table"):
            out.append(Finding("C05", "E", i + 1, f"表题行『{clip(ctx.lines[i])}』后未紧跟表格（下一非空行须以 | 起头）"))
        else:
            caps.setdefault(n, []).append(i + 1)
    has_table = False
    for i, k in enumerate(ctx.kinds):
        if k == "table" and (i == 0 or ctx.kinds[i - 1] != "table"):
            has_table = True
            j = i - 1
            while j >= 0 and ctx.kinds[j] == "blank":
                j -= 1
            if not (j >= 0 and ctx.kinds[j] == "tabcap"):
                out.append(Finding("C05", "E", i + 1, "表格前无表题行『表N 〈题名〉』"))
    for n, ls in sorted(caps.items()):
        if len(ls) > 1:
            out.append(Finding("C05", "E", ls[1], f"表编号 {n} 重复定义（L{ls[0]} 与 L{ls[1]}）"))
    nums = sorted(caps)
    if nums and nums != list(range(1, len(nums) + 1)):
        out.append(Finding("C05", "E", None, f"表编号不连续、重复或乱序：实际 {nums}，应为 {list(range(1, len(nums) + 1))}"))
    if not caps and not has_table:
        out.append(Finding("C05", "I", None, "无表格"))
    return out


# ---------------------------------------------------------------- C06 先文后图
FIG_REF_RE = re.compile(r"如图\s*(\d+)\s*所示|见图\s*(\d+)|图\s*(\d+)\s*所示")
TAB_REF_RE = re.compile(r"如表\s*(\d+)\s*所示|见表\s*(\d+)|表\s*(\d+)\s*(?:给出|列出|所示)")


def _collect_refs(ctx: Ctx, regex: re.Pattern) -> list[tuple[int, int, int]]:
    refs: list[tuple[int, int, int]] = []
    for i in ctx.body_indices:
        if ctx.kinds[i] in ("figcap", "tabcap"):
            continue
        for m in regex.finditer(ctx.lines[i]):
            n = next(int(g) for g in m.groups() if g is not None)
            refs.append((n, i + 1, m.start()))
    return refs


def check_c06(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    fig_defs: dict[int, tuple[int, int]] = {img.number: (img.line, img.col) for img in ctx.images if img.number is not None}
    for i in ctx.fig_caption_indices:
        fig_defs.setdefault(int(FIG_CAP_RE.match(ctx.lines[i]).group(1)), (i + 1, 0))
    tab_defs: dict[int, tuple[int, int]] = {}
    for i in ctx.tab_caption_indices:
        tab_defs.setdefault(int(TAB_CAP_RE.match(ctx.lines[i]).group(1)), (i + 1, 0))
    for kind, defs, regex in (("图", fig_defs, FIG_REF_RE), ("表", tab_defs, TAB_REF_RE)):
        refs = _collect_refs(ctx, regex)
        for n, (dl, dc) in sorted(defs.items()):
            if not any(rn == n and (rl, rc) < (dl, dc) for rn, rl, rc in refs):
                out.append(Finding("C06", "E", dl, f"{kind}{n} 之前无正文引称（先文后图：正文须先出现『如{kind}{n}所示』或『见{kind}{n}』）"))
        reported: set[int] = set()
        for n, rl, rc in refs:
            if n not in defs and n not in reported:
                reported.add(n)
                ascii_at = next((s + 1 for s, e in code_blocks(ctx)
                                 if any(re.search(rf"{kind}\s*{n}(?!\d)", ctx.lines[i]) for i in range(s + 1, e))), None)
                hint = f"；{kind} {n} 目前是代码块 L{ascii_at} 内的 ASCII 图，改为 ![](figs/fig{n}.png) 插图并补图注后即可通过" if ascii_at else ""
                out.append(Finding("C06", "E", rl, f"引称了不存在的{kind}{n}（验证点 8）{hint}", snippets(ctx.lines[rl - 1], rc)))
    return out


# ---------------------------------------------------------------- C07 段首缩进
def check_c07(ctx: Ctx) -> list[Finding]:
    hits = [Finding("C07", "E", i + 1, "正文段落段首须为两个全角空格『　　』（标题、图注、表格、列表、引言不缩进）",
                    clip(ctx.lines[i], 24))
            for i in ctx.para_indices
            if not (ctx.lines[i].startswith(INDENT) and not ctx.lines[i][2:].startswith("　"))]
    return collapse("C07", hits)


# ---------------------------------------------------------------- C08 标题层级体例
HEAD_STYLE = [
    ("一、", re.compile(r"^[一二三四五六七八九十]+、")),
    ("（一）", re.compile(r"^（[一二三四五六七八九十]+）")),
    ("(一)", re.compile(r"^\([一二三四五六七八九十]+\)")),
    ("1.1.1", re.compile(r"^\d+\.\d+\.\d+")),
    ("1.1", re.compile(r"^\d+\.\d+(?![.\d])")),
    ("1．", re.compile(r"^\d+．")),
    ("1.", re.compile(r"^\d+\.(?![\d．])")),
]
EXPECTED_STYLE = {2: "一、", 3: "（一）", 4: "1．", 5: "1.1"}
EXPECTED_MD = {2: "## 一、", 3: "### （一）", 4: "#### 1．", 5: "##### 1.1"}
UNNUMBERED_OK = ("摘要", "参考文献", "附录")


def head_style(text: str) -> str | None:
    for name, rx in HEAD_STYLE:
        if rx.match(text):
            return name
    return None


def check_c08(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    for idx, lvl, text in ctx.headings:
        if lvl == 1:
            continue
        style = head_style(text)
        if style is None:
            if lvl == 2 and text.startswith(UNNUMBERED_OK):
                continue
            out.append(Finding("C08", "W", idx + 1, f"{lvl} 级标题『{clip(text)}』未带层级编号，默认体例为 {EXPECTED_MD.get(lvl, '不超过 5 级')}；模板固定栏目可忽略"))
            continue
        if lvl > 5:
            out.append(Finding("C08", "W", idx + 1, f"标题『{clip(text)}』层级过深（{lvl} 级），体例只到 ##### 1.1"))
            continue
        if style == EXPECTED_STYLE[lvl]:
            continue
        if style == "1.1" and lvl == 4:
            msg = f"四级标题『{clip(text)}』用了『1.1』式编号，应为五级标题 {EXPECTED_MD[5]}"
        elif style == "1." and lvl == 4:
            msg = f"四级标题『{clip(text)}』用了半角句点，体例为全角『1．』（#### 1．）"
        elif style == "(一)" and lvl == 3:
            msg = f"三级标题『{clip(text)}』用了半角括号，体例为全角『（一）』（### （一））"
        elif style == "1.1.1":
            msg = f"标题『{clip(text)}』用了三级小数编号，体例只到 1.1"
        else:
            msg = f"{lvl} 级标题『{clip(text)}』的编号体例为『{style}』，应为 {EXPECTED_MD[lvl]}"
        out.append(Finding("C08", "W", idx + 1, msg))
    return out


# ---------------------------------------------------------------- C09 字数双口径
NON_BODY_KINDS = ("heading", "figcap", "tabcap", "table", "image", "quote", "code", "fence", "blank")


def check_c09(ctx: Ctx) -> list[Finding]:
    start = ctx.abs_heading + 1 if ctx.abs_heading is not None else 0
    end = ctx.ref_heading if ctx.ref_heading is not None else len(ctx.lines)
    body_chars = sum(cjk_count(ctx.lines[i]) for i in range(start, end) if ctx.kinds[i] not in NON_BODY_KINDS)
    full_chars = cjk_count("\n".join(ctx.lines))
    ctx.word_count = {"body": body_chars, "full": full_chars}
    out = [Finding("C09", "I", None, f"字数：论证正文 {body_chars} 字 / 全文 {full_chars} 字（汉字按字符计；论证正文 = 摘要至参考文献前，不含标题、图注、表格、图片行、引言、代码块）")]
    if ctx.max_body is not None and body_chars > ctx.max_body:
        out.append(Finding("C09", "W", None, f"论证正文 {body_chars} 字超过上限 {ctx.max_body} 字，须删 {body_chars - ctx.max_body} 字（不另起一段消化）"))
    return out


# ---------------------------------------------------------------- C10 引用首现顺序
CITE_RE = re.compile(r"\[(\d+)(?:\s*[-–—,，]\s*(\d+))?\]")


def check_c10(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    first: dict[int, tuple[int, int]] = {}
    order_errors: list[Finding] = []
    expected = 1
    for i in ctx.body_indices:
        if ctx.kinds[i] == "heading":
            continue
        line = ctx.lines[i]
        for m in CITE_RE.finditer(line):
            n = int(m.group(1))
            if m.group(2):
                out.append(Finding("C10", "E", i + 1, f"引用区间 {m.group(0)} 不允许，须逐个列出（如 [{n}][{m.group(2)}]）", snippets(line, m.start())))
            if n in first:
                continue
            first[n] = (i + 1, m.start())
            if n != expected:
                order_errors.append(Finding("C10", "E", i + 1, f"引用 [{n}] 首现早于 [{expected}]（引用编号须按正文首现顺序 1,2,3,… 连续）", snippets(line, m.start())))
            else:
                expected += 1
    out.extend(collapse("C10", order_errors))
    if ctx.ref_heading is None:
        out.append(Finding("C10", "E", None, "缺『## 参考文献』节"))
        return out
    ref_ids: dict[int, int] = {}
    for i in range(ctx.ref_heading + 1, len(ctx.lines)):
        if ctx.in_ref[i] and not ctx.in_code[i]:
            m = REF_LINE_RE.match(ctx.lines[i])
            if m:
                ref_ids[int(m.group(1))] = i + 1
    missing = sorted(set(first) - set(ref_ids))
    if missing:
        out.append(Finding("C10", "E", None, f"正文引用了但参考文献缺条目：{missing}"))
    unused = sorted(set(ref_ids) - set(first))
    if unused:
        out.append(Finding("C10", "W", None, f"参考文献有条目但正文未引用：{unused}（删除条目或在正文补引）"))
    nums = sorted(ref_ids)
    if nums and nums != list(range(1, len(nums) + 1)):
        shown = "、".join(map(str, nums[:12])) + ("…" if len(nums) > 12 else "")
        out.append(Finding("C10", "W", None, f"参考文献编号不连续：{shown}（共 {len(nums)} 条，按首现顺序应为 1..{len(nums)}）"))
    out.append(Finding("C10", "I", None, f"正文引用 {len(first)} 个编号，参考文献 {len(ref_ids)} 条"))
    return out


# ---------------------------------------------------------------- C11 口径一致
TOTAL_MARK = re.compile(r"合计|总计|累计|共计|总数|共\s*\d")


def _quant_hits(ctx: Ctx, unit: str) -> list[tuple[int, int, str]]:
    """正文中所有『N〈unit〉』：返回 (数值, 行号, 行文本)。"""
    rx = re.compile(r"(\d+)\s*" + unit)
    hits: list[tuple[int, int, str]] = []
    for i in ctx.body_indices:
        if ctx.kinds[i] == "heading":
            continue
        for m in rx.finditer(ctx.lines[i]):
            hits.append((int(m.group(1)), i + 1, ctx.lines[i]))
    return hits


def _lines_text(ls: list[int]) -> str:
    return "L" + " L".join(map(str, ls[:6])) + ("…" if len(ls) > 6 else "")


def check_c11(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    for unit, name in (("套", "原型套数"), ("份", "报告份数")):
        hits = _quant_hits(ctx, unit)
        if not hits:
            out.append(Finding("C11", "I", None, f"{name}：正文未出现『N {unit}』"))
            continue
        by_val: dict[int, list[int]] = {}
        for v, l, _ in hits:
            by_val.setdefault(v, []).append(l)
        if len(by_val) > 1:
            desc = "、".join(f"{v} {unit}×{len(ls)}（{_lines_text(ls)}）" for v, ls in sorted(by_val.items()))
            out.append(Finding("C11", "W", None, f"{name}出现 {len(by_val)} 种取值：{desc}；若为『合计 N {unit} / 每项 1 {unit}』属正当，否则统一口径（原则 7）"))
        else:
            out.append(Finding("C11", "I", None, f"{name}：{next(iter(by_val))} {unit}（{len(hits)} 处）"))

    hits = _quant_hits(ctx, "篇")
    sections: list[tuple[str, str, int, list[tuple[int, int, str]]]] = []
    heads: dict[str, int] = {}
    for name in ("摘要", "研究目标", "成果", "周期"):
        rng = ctx.section(name)
        if rng is None:
            continue
        sec = [(v, l, t) for v, l, t in hits if rng[0] <= l - 1 < rng[1]]
        if not sec:
            continue
        total_vals = [v for v, _, t in sec if TOTAL_MARK.search(t)]
        if total_vals:
            head, mode = total_vals[0], "合计行"
        elif len({v for v, _, _ in sec}) == 1:
            head, mode = sec[0][0], "单一取值"
        else:
            head, mode = sum(v for v, _, _ in sec), "分项之和"
        heads[name] = head
        sections.append((name, mode, head, sec))
    if not sections:
        out.append(Finding("C11", "I", None, "论文篇数：正文未出现『N 篇』"))
    else:
        desc = "；".join(f"{name} {head} 篇（{mode}，{_lines_text([l for _, l, _ in sec])}）" for name, mode, head, sec in sections)
        out.append(Finding("C11", "I", None, f"论文篇数：{desc}"))
        if len(set(heads.values())) > 1:
            out.append(Finding("C11", "E", None, f"论文篇数口径不一致（原则 7 口径唯一）：{desc}"))
    for name, mode, head, sec in sections:
        if name == "成果" and mode == "合计行":
            items = [v for v, _, t in sec if not TOTAL_MARK.search(t) and v != head]
            if len(items) >= 2 and sum(items) != head:
                out.append(Finding("C11", "W", None, f"成果节分项论文篇数之和 {'+'.join(map(str, items))}={sum(items)}，与合计 {head} 篇不一致"))
    return out


# ---------------------------------------------------------------- C12 分类命名一致
CLASS_RE = re.compile(
    r"按(?P<dim>[^，。：,]{2,60})[，,]?\s*(?P<mid>[^，。：,]{0,20}?)"
    r"(?P<verb>分为|划分为|分成|形成)(?P<n>[一二三四五六七八九十0-9]{1,3})(?P<cls>类|条|个|种|项)")
LAYER_WORDS = ("层", "模块", "环节", "方面", "阶段", "层级")
METHOD_WORDS = ("方法", "机制", "路线", "策略")
# 排除『分类方法/子类方法』这类词内误报
LAYER_BAD = re.compile(r"(?<![分小子])类方法|(?<!分)种方法|(?<!分)条技术路线|(?<!分)类技术路线|(?<!分)类路线|(?<!分)条路线")
METHOD_BAD = re.compile(r"个层次|个层级|个层(?!次|级)|个模块")


def check_c12(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    sents: list[tuple[int, re.Match]] = []
    for i in ctx.body_indices:
        if ctx.in_code[i]:
            continue
        for m in CLASS_RE.finditer(ctx.lines[i]):
            sents.append((i + 1, m))
    if not sents:
        return [Finding("C12", "W", None, "未检出显式分类句『按〈分类维度〉分为 N 类：〈A〉、〈B〉…』（验证点 3：分类标准必须显式成句）")]
    for line_no, m in sents:
        dim = m.group("dim")
        layer_hits = [w for w in LAYER_WORDS if w in dim]
        method_hits = [w for w in METHOD_WORDS if w in dim]
        if layer_hits:
            kind, bad_rx, fix = "按模块/层次分类", LAYER_BAD, "N 个层次/N 个方面/N 类研究"
        elif method_hits:
            kind, bad_rx, fix = "按方法/路线分类", METHOD_BAD, "N 类方法/N 条技术路线"
        else:
            kind, bad_rx, fix = "未识别分类性质", None, ""
        out.append(Finding("C12", "I", line_no,
                           f"分类句：维度『{clip(dim, 40)}』→ {kind}（命中：{'、'.join(layer_hits + method_hits) or '无'}）；"
                           f"量词『{m.group('cls')}』，数量『{m.group('n')}』"))
        if bad_rx is None:
            continue
        for i in ctx.body_indices:
            if i + 1 == line_no or ctx.kinds[i] == "heading":
                continue
            seen: set[str] = set()
            for h in bad_rx.finditer(ctx.lines[i]):
                if h.group(0) in seen:
                    continue
                seen.add(h.group(0))
                out.append(Finding("C12", "W", i + 1,
                                   f"{kind}时不得称『{h.group(0)}』，应改为『{fix}』（分类维度与量词一致，验证点 3）",
                                   snippets(ctx.lines[i], h.start())))
    return out


# ---------------------------------------------------------------- C13 研究内容时态
DONE_RE = re.compile(r"已实现|已完成|已搭建|已开发|已建成|已支持")
SENT_END = "。；！？"


def check_c13(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    rng = ctx.section("研究内容")
    if rng is None:
        return [Finding("C13", "W", None, "未检出『## 研究内容』节")]
    for i in ctx.paras_in(*rng):
        line = ctx.lines[i]
        for m in DONE_RE.finditer(line):
            start = max([line.rfind(p, 0, m.start()) for p in SENT_END] + [-1]) + 1
            ends = [x for x in (line.find(p, m.end()) for p in SENT_END) if x != -1]
            sent = line[start: min(ends) if ends else len(line)]
            if re.search(r"研究基础|前期|已有", sent):
                out.append(Finding("C13", "W", i + 1, f"研究内容节出现完成时『{m.group(0)}』（句含『前期/已有/研究基础』，若为交代前期基础可放行，否则改为拟开展）", clip(sent)))
            else:
                out.append(Finding("C13", "E", i + 1, f"研究内容节用了完成时『{m.group(0)}』，时态须为拟开展（研究/建立/提出/揭示）；完成时只属于团队研究基础节", clip(sent)))
    return out


# ---------------------------------------------------------------- C14 风险措辞
NEG_RE = re.compile(r"失败|不收敛|失真|崩溃|不可用|无法达成|流产")
CONTROL_RE = re.compile(r"控制|化解|兜底|保底|降级|替代")
RISK_STMT_RE = re.compile(r"风险[：:]|风险在于|风险为")


def check_c14(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    rng = ctx.section("风险")
    if rng is None:
        return [Finding("C14", "W", None, "未检出『## 风险』节（模板含此栏目时适用）")]
    paras = ctx.paras_in(*rng)
    text = "\n".join(ctx.lines[i] for i in paras)
    for i in paras:
        seen: set[str] = set()
        for m in NEG_RE.finditer(ctx.lines[i]):
            if m.group(0) in seen:
                continue
            seen.add(m.group(0))
            out.append(Finding("C14", "W", i + 1, f"风险后果措辞过于绝对『{m.group(0)}』，改为可识别可控制的过程描述并紧跟控制/兜底句（原则 10）",
                               snippets(ctx.lines[i], m.start())))
    if not re.search(r"总体可控|风险可控", text):
        out.append(Finding("C14", "W", None, "缺风险可控收束句（末句应收『…总体风险可控』）"))
    n_stmt = 0
    for i in paras:
        line = ctx.lines[i]
        for m in RISK_STMT_RE.finditer(line):
            n_stmt += 1
            if not CONTROL_RE.search(line[m.end():]):
                out.append(Finding("C14", "E", i + 1, f"风险无控制手段：『{clip(line[max(0, m.start() - 12):m.end() + 20])}』之后同段须紧跟控制手段（控制/化解/兜底/保底/降级/替代）"))
    if n_stmt == 0:
        out.append(Finding("C14", "W", None, "未检出『〈风险名〉风险：…』成对句；每类风险写成「风险：控制手段 + 兜底方案」"))
    return out


# ---------------------------------------------------------------- C15 周期段结构
CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn2int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if "十" in s:
        a, b = s.split("十", 1)
        return (CN_DIGITS.get(a, 1) if a else 1) * 10 + (CN_DIGITS.get(b, 0) if b else 0)
    return CN_DIGITS.get(s)


STAGE_RE = re.compile(r"^(?:第[一二三四五六七八九十0-9]+阶段|阶段[一二三四五六七八九十0-9]+)")
DECL_RE = re.compile(r"(?:分为|划分(?:为)?)\s*([0-9一二三四五六七八九十]+)\s*个阶段")


def check_c15(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    rng = ctx.section("周期")
    if rng is None:
        return [Finding("C15", "W", None, "未检出『## 研究周期』节")]
    paras = ctx.paras_in(*rng)
    if not paras:
        return [Finding("C15", "W", None, "研究周期节无正文段落")]
    first = ctx.lines[paras[0]]
    if "思路" not in first:
        out.append(Finding("C15", "W", paras[0] + 1, "研究周期缺总体研发思路总起段（要先讲整体思路，再『根据…分为以下 N 个阶段，其中第 1 阶段完成…，第 2 阶段完成…』）", clip(first)))
    if not re.search(r"分为|划分", first):
        out.append(Finding("C15", "W", paras[0] + 1, "研究周期总起段未写『划分为 N 个阶段』", clip(first)))
    if "阶段" not in first:
        out.append(Finding("C15", "W", paras[0] + 1, "研究周期总起段未提『阶段』", clip(first)))
    m = DECL_RE.search(first)
    declared = cn2int(m.group(1)) if m else None
    stage_paras = [i for i in paras if STAGE_RE.match(ctx.lines[i].lstrip(INDENT + " "))]
    out.append(Finding("C15", "I", None, f"阶段段落数 {len(stage_paras)}" + (f"（总起段声明 {declared} 个）" if declared is not None else "")))
    if not stage_paras:
        out.append(Finding("C15", "W", None, "无以『阶段X（N 个月）：』起句的阶段段落"))
    if declared is not None and declared != len(stage_paras):
        out.append(Finding("C15", "W", paras[0] + 1, f"总起段声明 {declared} 个阶段，实际阶段段落 {len(stage_paras)} 段"))
    return out


# ---------------------------------------------------------------- C16 研究目标结构
SPLIT_RE = re.compile(r"分为|包括|可分解为|划分为")
REL_RE = re.compile(r"衔接|支撑|基础|闭环|递进|依赖|贯穿|协同")


def check_c16(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    rng = ctx.section("研究目标")
    if rng is None:
        return [Finding("C16", "W", None, "未检出『## 研究目标』节")]
    paras = ctx.paras_in(*rng)
    if not paras:
        return [Finding("C16", "W", None, "研究目标节无正文段落")]
    h_goal = ctx.subheading_after(*rng, "总目标")
    goal_idx = next((i for i in paras if h_goal is None or i > h_goal), paras[0])
    goal = ctx.lines[goal_idx]
    sm = SPLIT_RE.search(goal)
    if not sm:
        out.append(Finding("C16", "W", goal_idx + 1, "总目标未按分类写出『可分为 A、B、C、D』（通则 4 先总后分）", clip(goal)))
    if not REL_RE.search(goal):
        out.append(Finding("C16", "W", goal_idx + 1, "总目标未说明各分目标之间的关系（衔接/支撑/基础/闭环/递进/依赖/贯穿/协同）", clip(goal)))
    h_sub = ctx.subheading_after(*rng, "分目标")
    subs = [i for i in paras if i != goal_idx and ctx.lines[i].lstrip(INDENT + " ").startswith("针对")
            and (h_sub is None or i > h_sub)]
    n_items = 0
    if sm:
        # 枚举段：从『分为』起到第一个句读（。；，,）止，按『、』切分；『分为以下 N 个…』直接取 N
        tail = re.split(r"[。；，,]", goal[sm.end():])[0]
        tail = re.sub(r"^[\s：:为成]+", "", tail)
        nm = re.search(r"(?:以下|下列)?\s*([0-9一二三四五六七八九十]+)\s*个", tail)
        if nm and "、" not in tail:
            n_items = cn2int(nm.group(1)) or 0
        else:
            tail = re.sub(r"(?:[一二三四五六七八九十0-9]+个)?(?:分目标|子目标|方面|项|类|部分|目标)$", "", tail.strip())
            n_items = len([s for s in tail.split("、") if s.strip()])
    out.append(Finding("C16", "I", None, f"分目标段落数 {len(subs)}；总目标列项 {n_items}"))
    if not subs:
        out.append(Finding("C16", "W", None, "无以『　　针对…』起句的分目标段落（每条：针对〈不足 i〉，研究〈机制〉，建立〈可检查的产出〉）"))
    if sm and n_items != len(subs):
        out.append(Finding("C16", "W", goal_idx + 1, f"分目标段落数 {len(subs)} 与总目标列出的 {n_items} 项不一致（总说枚举须与分说逐条平行）"))
    return out


# ---------------------------------------------------------------- C17 术语一致
TERM_GROUPS = [("软件栈", "系统栈", "系统", "平台"), ("原型", "原型系统", "样机"),
               ("语义等价", "等价性", "等价"), ("论文", "文章")]


def check_c17(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    text = "\n".join(ctx.lines[i] for i in ctx.body_indices if ctx.kinds[i] != "heading")
    for group in TERM_GROUPS:
        # 互斥计数：长词优先匹配，避免「等价」⊂「语义等价」这类子串重叠误报
        rx = re.compile("|".join(re.escape(t) for t in sorted(group, key=len, reverse=True)))
        counts: dict[str, int] = {}
        for m in rx.finditer(text):
            counts[m.group(0)] = counts.get(m.group(0), 0) + 1
        if len(counts) >= 2:
            desc = "、".join(f"{t}×{c}" for t, c in sorted(counts.items(), key=lambda kv: -kv[1]))
            out.append(Finding("C17", "W", None, f"术语组『{'/'.join(group)}』出现 {len(counts)} 个词并用：{desc} — 请校对员确认是否指同一概念（同一概念全文用词唯一，由人裁定）"))
    return out


# ---------------------------------------------------------------- C18 图片分辨率
def check_c18(ctx: Ctx) -> list[Finding]:
    pngs = [img for img in ctx.images if img.resolved is not None and img.resolved.suffix.lower() == ".png"]
    if not pngs:
        return []
    if not ctx.pil:
        return [Finding("C18", "I", None, "Pillow 不可用，跳过像素宽度检查")]
    from PIL import Image as PILImage

    out: list[Finding] = []
    for img in pngs:
        label = f"图 {img.number}" if img.number is not None else Path(img.path).name
        try:
            with PILImage.open(img.resolved) as im:
                w, h = im.size
        except Exception as exc:  # 图片损坏等
            out.append(Finding("C18", "E", img.line, f"无法打开图片 {img.path}：{type(exc).__name__}: {exc}"))
            continue
        if w < 1400:
            out.append(Finding("C18", "W", img.line, f"{label} 像素宽 {w}<1400，按版心 16cm 放置时有效 DPI 偏低（{w}×{h}）"))
        else:
            out.append(Finding("C18", "I", img.line, f"{label} 像素 {w}×{h}，版心 16cm 下约 {w / 16 * 2.54:.0f} DPI"))
    return out


# ---------------------------------------------------------------- C19 段落长度
PARA_LIMIT = 450
ABSTRACT_LIMIT = 800


def check_c19(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    abs_rng = ctx.section("摘要")
    for i in ctx.para_indices:
        n = cjk_count(ctx.lines[i])
        if abs_rng and abs_rng[0] <= i < abs_rng[1]:
            if n > ABSTRACT_LIMIT:
                out.append(Finding("C19", "W", i + 1, f"摘要 {n} 字，超过 {ABSTRACT_LIMIT} 字上限", clip(ctx.lines[i])))
            continue
        if n > PARA_LIMIT:
            out.append(Finding("C19", "W", i + 1, f"段落 {n} 字，超过 {PARA_LIMIT} 字上限（写作规范单段 ≤400 字），拆分或压缩", clip(ctx.lines[i])))
    return out


# ---------------------------------------------------------------- C20 摘要位置
def check_c20(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    h2 = next(((idx, text) for idx, lvl, text in ctx.headings if lvl == 2), None)
    if h2 is None:
        out.append(Finding("C20", "E", None, "无二级标题；首个 ## 须为『## 摘要』（摘要单列，放在正文之前）"))
    elif not h2[1].startswith("摘要"):
        out.append(Finding("C20", "E", h2[0] + 1, f"首个二级标题须为『## 摘要』，实为『{clip(h2[1])}』（摘要单列，放在正文之前）"))
    first_idx = next((i for i, k in enumerate(ctx.kinds) if k not in ("blank", "quote")), None)
    if first_idx is None:
        out.append(Finding("C20", "E", None, "文件为空"))
    else:
        m = HEADING_RE.match(ctx.lines[first_idx])
        if not (m and len(m.group(1)) == 1 and m.group(2).strip()):
            out.append(Finding("C20", "E", first_idx + 1, "首行须为一级标题『# 〈项目名称〉』（第一个非空、非引言行）", clip(ctx.lines[first_idx])))
    titles = [idx for idx, lvl, _ in ctx.headings if lvl == 1]
    if len(titles) > 1:
        out.append(Finding("C20", "W", titles[1] + 1, f"出现 {len(titles)} 个一级标题，只保留文首项目名"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_md.py", description="申请书 markdown 机械校验 C01–C20")
    ap.add_argument("md", help="申请书 markdown 文件")
    ap.add_argument("--figs-dir", default=None, help="图片相对路径的备选根目录（默认 md 所在目录）")
    ap.add_argument("--json", dest="json_out", default=None, help="把全部发现写到该 JSON 文件")
    ap.add_argument("--quiet", action="store_true", help="不打印通过的检查项与 I 级提示")
    ap.add_argument("--max-body", type=int, default=None, help="论证正文汉字上限，超出记 W")
    ap.add_argument("--allow", default="", help="逗号分隔的检查项，其错误不计入退出码（如 C03,C04,C18）")
    args = ap.parse_args(argv)

    md_path = Path(args.md)
    if not md_path.is_file():
        print(f"[check] 结果: 无法读取 {md_path}", file=sys.stderr)
        return 2
    text = md_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    md_dir = md_path.resolve().parent
    figs_dir = Path(args.figs_dir).resolve() if args.figs_dir else md_dir
    try:
        import PIL  # noqa: F401
        pil = True
    except Exception:
        pil = False
    ctx = build_ctx(lines, md_dir, figs_dir, pil, args.max_body)

    findings: list[Finding] = []
    for code, _ in CHECKS:
        func = globals()[f"check_{code.lower()}"]
        try:
            findings.extend(func(ctx))
        except Exception as exc:  # 单项异常不拖垮整轮校验
            findings.append(Finding(code, "E", None, f"检查项内部异常：{type(exc).__name__}: {exc}"))
    findings.sort(key=lambda f: (f.check, (f.line is None), f.line or 0))  # 无行号的汇总行排在组内末尾

    allowed = {a.strip().upper() for a in args.allow.split(",") if a.strip()}
    errors = [f for f in findings if f.level == "E"]
    blocking = [f for f in errors if f.check not in allowed]
    warnings = [f for f in findings if f.level == "W"]

    out = sys.stdout
    for code, title in CHECKS:
        items = [f for f in findings if f.check == code and not (args.quiet and f.level == "I")]
        if not items:
            if not args.quiet:
                out.write(f"[check] -- {code} {title}: 通过\n")
            continue
        out.write(f"[check] == {code} {title}\n")
        for f in items:
            if args.quiet and f.level == "I":
                continue
            loc = f"L{f.line}" if f.line is not None else "L-"
            out.write(f"[check] {f.level}{code[1:]} {loc}: {f.message}\n")
    out.write(f"[check] 结果: 错误 {len(errors)} / 警告 {len(warnings)} / 检查项 {len(CHECKS)}\n")
    if allowed and any(f.check in allowed for f in errors):
        out.write(f"[check] 注: {'/'.join(sorted(allowed))} 的错误按 --allow 不计入退出码\n")

    if args.json_out:
        report = {
            "file": str(md_path),
            "summary": {"errors": len(errors), "warnings": len(warnings), "info": len(findings) - len(errors) - len(warnings),
                        "checks": len(CHECKS), "blocking_errors": len(blocking), "allowed": sorted(allowed)},
            "word_count": ctx.word_count,
            "findings": [asdict(f) for f in findings],
        }
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
