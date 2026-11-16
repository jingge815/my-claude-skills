# 模板实测规格（assets/template.pptx）

本文件是 `assets/template.pptx` 的实测说明书，也是 `scripts/build_ppt.py` 的行为契约。
模板复制自 proposalword2ppt 的答辩模板，版式、背景、ISCAS 标识、字体、配色全部保留，
按论文总结的需要重排为 **5 张蓝图页**。改动模板用 `scripts/rebuild_template.py` 重建；
几何若变，须同步改 `build_ppt.py` 的 `KIND_SLOTS` / `FIGURE_*` / `SUMMARY_*` 常量与本文件。

所有数值从模板 XML 量出。单位：厘米（1 cm = 360000 EMU）。

## 一、模板概况

- 画布 33.87 × 19.05 cm（16:9 宽屏），5 张蓝图：
  `cover`(slide1) / `text-left`(slide2) / `status`(slide3) / `figure-left`(slide4) / `closing`(slide5)。
- 第 2–5 张统一版式 `10_自定义版式`：深色背景图、右上角 ISCAS 标识、右下角自动页码
  由**版式**提供；slide1 用 `标题幻灯片` 版式。
- **同一 kind 的页可无限复用**：content.json 里方法讲 3 页就写 3 个 `figure-left` 页，
  不需要改模板。
- 母版标题样式：微软雅黑 + Times New Roman，28 磅加粗，白色。
- 配色为 Office 标准主题色（accent1 `#5B9BD5`、accent2 `#ED7D31`、accent5 `#4472C4`、
  accent6 `#70AD47`、accent4 `#FFC000`）。图表由 `shapes.py` 按主题色生成原生形状，
  不要引入模板外配色。

## 二、页面蓝图与槽位几何

| kind | 版式 | 槽位布局 |
|---|---|---|
| cover | 标题幻灯片 | 标题版式框 (4.23,3.12) 25.4×6.63，**脚本加宽为 (1.7,3.12) 30.47×6.63**（长题目单行放下、避免孤行）；副标题 (4.23,10.01) 25.4×4.60；汇报人框脚本改到 (21.2,14.75) 11.4×3.35 |
| text-left | 自定义 | 标题 (1.13,0.32) 25.76×1.43；正文 (1.13,4.13) 12.31×10.54；图槽 (14.58,4.89) 18.16×9.27；总结 (2.78,17.05) 30.55×1.13 |
| status | 自定义 | 标题同上；正文 (1.45,4.10) 10.72×11.66；图槽1 (13.13,2.31) 19.37×7.97；图槽2 (13.13,10.63) 19.37×7.97；**无总结槽** |
| figure-left | 自定义 | 标题同上；图槽 (1.13,4.89) 18.16×9.27；正文 (19.70,4.13) 12.31×10.54；总结 (2.78,17.05) 30.55×1.13 |
| closing | 自定义 | 标题 (0.93,0.33)；Take-away 句 + 出处行 (4.73,4.21) 24.4×2.31（楷体 24 / 15 磅两段，可 {{红粗}}）；「感谢聆听」(1.17,11.18) 31.53×1.85（华文中宋 36 磅，脚本强制居中）；ISCAS 标识图 (14.11,14.02) |

正文与图槽垂直对齐由脚本完成：正文上沿抬到图槽上沿，下沿收到总结框上方 0.3 cm；
text-left 正文右沿收到图槽左沿前 0.4 cm；figure-left 正文左沿贴到图槽右沿 +1.0 cm。

## 三、字体与字号

字号**不固定**，族名跟模板，磅值由脚本按框几何与字数适配。

| 位置 | 中文字体 | 基准 / 范围 |
|---|---|---|
| 页面标题 | Microsoft YaHei | 基准 28 磅，超长 24 / 20 |
| 封面标题 | 微软雅黑 | 默认 44 磅；超长降 40 / 36 / 32，居中 |
| 封面副标题 | 主题字体 | 24 磅 |
| 封面右下角 | 楷体 | 三行统一 20 磅 |
| 正文一级 | 楷体 | 18 磅（固定）；项目符号 `▶`，文字 1.0 cm 起；**下划线只给标签（含冒号）** |
| 正文二级 | 楷体 | 16 磅（比一级小 2 号）；项目符号 `●`；文字 2.13 cm 起（= 一级文字 + 2 个 16 磅字宽），无下划线 |
| 底部总结 | 楷体 | 默认 16，按框适配 12–16 |
| 图内文字 | 楷体 | 16–18 磅（下限 14） |
| 感谢语 | 华文中宋 | 36 磅 |

正文要点的 pPr 由 build_ppt 整体重写（marL/indent/项目符号），不依赖模板段落；
一二级符号挂 `buChar`（Arial 字体）。投影可读下限 12 磅；正文一级/二级固定
18/16 磅不自适应，放不下按「减字数 → 砍二级行」处理，不压字号。

## 四、字数上限（建议值，机械校验会报）

| 项 | 上限 |
|---|---|
| 每页要点条数 | 6 条（至少 3 条） |
| 每条主句（标题式短语，禁句号） | 20 字宽（现状页带引用 24） |
| 二级行 | 22 字宽 × 3 条 |
| 要点层级 | ≤2 级（标签 + 一级主句 + 二级说明） |
| 底部总结句 | 74 字（一行放下为宜，压到 14 磅以下要改短） |
| 封面标题 | 每行 16 字（44 磅） |
| 红粗强调 `{{}}` | 每个内容页 ≥1 处（颜色 C00000），1–4 处为宜 |

## 五、content.json 契约

```json
{
  "title": "论文题目（写入文档属性与旁白稿标题）",
  "pages": [ 按放映顺序；第 1 页 cover，最后一页 closing ],
  "narration": { "1": "第 1 页旁白", "2": "…" }
}
```

各 kind 字段：

| kind | 必填 | 选填 |
|---|---|---|
| cover | title | subtitle（默认「论文总结汇报」，应写发表信息）、presenter、date、org（默认中国科学院软件研究所） |
| text-left / status / figure-left | title、points、figure | summary（status 页无总结槽，写了也不渲染） |
| refs | refs（`[N] 作者. 题目. 出处 年份` 数组，≤10 条，编号与正文引用对应） | title（默认「参考文献」）。复用 text-left 蓝图：删图槽、清总结槽、正文框拉通全宽 (1.13, 4.13) 31.57×12.40 cm，条目字号 12–14 磅自适应 |
| closing | — | title（默认「要点回顾」）、summary（一句话 Take-away，可 {{红粗}}）、info（论文出处一行：英文原题 · 会议/期刊 年份 · 论文网址 · 代码链接若有）、thanks（默认「感谢聆听」，居中） |

- `points` 的 text 与 `subs`、closing 的 summary/info 支持**重点标记**：
  `{{文字}}` → 红色（C00000）加粗；`**文字****` → 加粗。每个内容页至少一处 `{{}}`。

- `points` 每项是字符串「标签：主句」或 `{"label","text","subs":[...]}`。
- `figure` 是一个图表对象、一个图片路径字符串（如 `"figs/fig5.png"`）或对象数组。
  status 版式原生 2 个图槽（框架图 + 表各一）；**其余版式 1 个图槽也可给 2~3 组图**，
  生成时自动上下切分（split_slot，格间 0.3 cm），用于把稀疏页排饱满。
  选型规则见 `references/figure-spec.md`。
- `narration` 也可以是数组；省略时备注页为空（校验会报）。

## 六、脚本清单（按流程顺序）

**输出文件名用论文题目**（`check_ppt` 校验 pptx 后缀）：`<论文题目>-slide.pptx`、
`<论文题目>-旁白.md/.docx`。论文题目取 `extract/meta.json` 的 `title_guess`，清理规则
与 paper-reader 一致（空格换 `_`，`/\:*?"<>|` 换 `-`，去首尾标点，超 60 字符在词边界
截断）。例：题目 `AttAcc: A Sparsity-Aware Accelerator` →
`AttAcc_A_Sparsity-Aware_Accelerator-slide.pptx`。

```bash
PY=<skill目录>/.venv/bin/python; S=<skill目录>/scripts

# 1）提取论文全文、首页封面与图表裁剪（cover.png + meta.json 含 title_guess/link_candidates）
$PY $S/extract_pdf.py 论文.pdf --out <工作目录>

# 2）生成 PPT（旁白写入备注页，另出旁白稿 md）
$PY $S/build_ppt.py --content content.json --out <论文题目>-slide.pptx \
        --narration-out <论文题目>-旁白.md

# 3）门禁1：结构与内容（python-pptx 重新打开成品交叉验证；含参考文献对应、有效 DPI、图必讲）
$PY $S/check_ppt.py <论文题目>-slide.pptx --content content.json

# 4）门禁2：旁白稿机械校验（结构/预算/格式/口语/数字溯源）
$PY $S/check_narration.py <论文题目>-旁白.md --extract <工作目录>/extract

# 5）门禁3：逐页出预览图并程序化检测版面（区域空白/图偏小）
$PY $S/preview_slides.py --pptx <论文题目>-slide.pptx --out-dir preview
$PY $S/inspect_preview.py preview --content content.json

# 6）旁白稿转 docx
$PY $S/build_docx.py <论文题目>-旁白.md

# 7）进入阶段 6 迭代：发现问题改 content.json/figs → 回到 2）重新生成，直到三道门禁错误清零
```

依赖：pymupdf、python-pptx、python-docx、pillow、numpy（`.venv` 已装，见 requirements.txt）。
`build_ppt.py` 生成时打印逐页报告（要点条数/字数、字号、图是原图还是形状图、旁白字数），
`[校验]` 行是问题清单；preview 有 LibreOffice 走 soffice，否则用 Pillow 近似预览
（字体与换行有差异，只看布局）。

## 七、模板维护

- 改模板：改 `scripts/rebuild_template.py`（或直接在 PowerPoint 里编辑后另存），
  重跑生成 `assets/template.pptx`；蓝图页号/槽位名/几何变化须同步
  `build_ppt.py` 顶部常量（`BLUEPRINTS`、`KIND_SLOTS`、`FIGURE_TOP/BOTTOM/LEFT/RIGHT`、`SUMMARY_TOP`）与本文件。
- 槽位靠形状名定位（`标题 1`、`副标题 2`、`文本框 3`、`文本框 1/31/33`、`矩形 2/8`、`文本框 6`）。
- 不能动的：版式提供的背景/标识/页码、主题配色与字体族、总结框高度（1.13 cm）。
