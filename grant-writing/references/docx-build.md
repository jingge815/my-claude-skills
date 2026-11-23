# docx 生成技术要点

docx 是默认交付格式（与 md 并列），由脚本从 md 生成，md 是单一事实源。构建员必读。

## 流水线（build / verify 分离）

```bash
PY=~/.claude/skills/grant-writing/.venv/bin/python
S=~/.claude/skills/grant-writing/scripts
$PY $S/build_docx.py   <grant目录>/05-申请书.md --out <grant目录>/05-申请书.docx [--template <模板.docx>]
$PY $S/verify_docx.py  <grant目录>/05-申请书.docx <grant目录>/05-申请书.md [--allow-prefix N] [--json <grant目录>/scripts/verify_docx.json]
```

- 构建脚本只做生成；校验脚本断言「docx 与 markdown 源逐段一致 + 表格数值自洽 + 图片行距与尺寸 + 引用首现顺序 + pPr 子元素顺序 + 首行缩进 + 标题字号 + 有效 DPI」，每轮修订后重跑，作为回归检查。
- 两个脚本任一退出码非 0，不交付。
- 本机无 Word/LibreOffice 时以结构校验为准，并在交付说明中提醒用户开 Word 终验图片显示。

## 无模板时的版式（build_docx.py 默认）

- 页面 A4，左右边距 2.5 cm、上下 2.54 cm，版心宽 16.0 cm。
- 正文宋体 / Times New Roman 小四 12pt，1.5 倍行距，两端对齐，首行缩进 2 字符（`w:ind firstLineChars=200`）。
- 标题：文档题目黑体三号 16pt 居中；`## 一、` 黑体四号 14pt；`### （一）` 黑体小四 12pt；`#### 1．` 与 `##### 1.1` 宋体 12pt 加粗。
- 图注、表注五号 10.5pt 加粗居中；表格单元格 10.5pt，表头浅灰底 `EDEDED`；参考文献五号、悬挂缩进、1.3 倍行距。

## 有模板时

- 用 `--template` 以模板为底稿：加载模板后在其正文之后追加内容，页眉页脚、页边距、样式、表格、推荐意见表原样保留；版心宽度从模板 `sectPr` 的 `pgSz − pgMar` 读取，不写死。
- 模板说明段（斜体、括号提示、填表说明）保留原位，填写内容插在其后；`verify_docx.py` 用 `--allow-prefix N` 跳过这些模板原有段落（N = 模板保留的非空段数，构建员数一次写进构建记录）。
- 模板要求填入固定表格（成员表、经费表）时，构建员在 build 之后用 python-docx 单独填表（不经过 md），填表逻辑写成 `<grant目录>/scripts/fill_template.py` 以便每轮重跑；表格空行不足时深拷贝现有空行扩展，gridSpan 合并单元格只填一次文本。

## 实测踩过的坑（逐条执行）

- **图片段落必须自动行距。** Word 对 `lineRule="exact"`（固定值行距）段落里的内嵌图片只显示一个行高，图会「消失」成一条细缝——这是图无法显示的最常见根因。插图前把该段 `w:spacing` 改为 `lineRule="auto"` 并去掉 `w:line`。`verify_docx.py` V03 断言。
- **图片宽度不超过版心。** 版心宽度 = `pgSz − pgMar`（twips，1 twip = 635 EMU，1 cm = 360000 EMU），按版心宽度等比缩放，不按原图像素写死；高度上限 22 cm；像素宽 <700 的图不放大到超过自然尺寸。V04 断言。
- **pPr 子元素顺序。** OOXML schema 规定顺序（keep … spacing, ind, … jc, … rPr, sectPr）；`w:jc` 必须位于 `w:rPr` 之前，`w:spacing` 在 `w:ind` 之前。手工插 `w:ind`/`w:spacing` 时用脚本的 `ppr_insert()` 按 schema 顺序插入。Word 多半容忍乱序，但不要赌。V10 断言。
- **模板部件不动。** 以模板为底稿，只改需改的部件。
- **图片用 add_picture 插入。** 段落文本用 lxml 构造以继承模板样式，图片用 python-docx 的 `add_picture` 插入（先入树再取段落对象加 run，避免 parent 为空），关系与 Content-Types 由其自动维护。
- **表格**：空行不足时深拷贝现有空行扩展；gridSpan 合并单元格只填一次文本；填表前先读表格自带注释决定口径（如包干制只填总额与年度计划）。
- **python-docx 陷阱。** 其 oxml 代理树上 `itertext` 对合并单元格会返回重复文本，表格断言改在纯 `lxml.fromstring` 解析树上执行；`id()` 在 lxml 代理对象上会因地址复用给出假相等，不用它判断元素同一性。
- **md 里的 fenced code block** 会被构建脚本以等宽字体原样渲染并打印警告——最终稿不应有代码块（ASCII 图禁止）；看到该警告回 `check_md.py` C03。

## 字体可用性

正文用「宋体 / 黑体」是 Word 侧字体名，本机 Linux 不需要安装；`w:eastAsia` 属性写进去后由用户的 Word 渲染。本机只在校验阶段读 XML，不渲染页面。

## 交付说明模板（构建员回报）

```
docx 构建：<路径>（标题 N，段落 N，插图 N 张，表格 N 个，参考文献 N 条）
模板模式：无 / 以 <模板名> 为底稿，allow-prefix N
verify：错误 0 / 警告 M（逐条列出，确属合理放行的说明原因）
提醒：本机无 Word，请用 Word 打开核对图片显示、分页与表格样式。
```
