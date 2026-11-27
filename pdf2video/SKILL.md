---
name: pdf2video
description: 当用户提供一篇学术论文 PDF，要求生成讲解视频（mp4）时使用本技能。触发场景包括"把这篇论文做成讲解视频""论文转视频""用pdf2video生成""论文解说视频""论文视频带旁白字幕"等。本技能先调用 pdf2ppt 技能生成 20–24 页论文总结 PPT 与 25–30 分钟旁白（已有产物则复用并校验），经视频版适配（删概述页去重复、清汇报人与单位、旁白不念网址）后转成 B 站风格讲解视频：CosyVoice2 本地女声解说（数字读法归一化，342 念三百四十二）、字幕与声音和画面逐页对应、讲解光标按"图 N"编号精确指图（ref 驱动+分句级粒度）、长讲图段从 4K 底图局部放大、轻快背景音乐自动闪避；模板固化在 config（背景音/主播音/音量/字幕字体/封面模板）。生成后由独立审核 agent 迭代校对（清晰度/音画字对应/指图一致性/女声/背景音/封面图文不重叠六组门禁 + 视觉评审），修复后重新生成直到全部问题清零。只处理学术论文 PDF；只出 PPT 不出视频走 pdf2ppt，纯文字总结走 paper-reader。
---

# 论文 PDF 转讲解视频（pdf2video）

把一篇论文 PDF 变成一支 **B 站风格讲解视频 mp4**：封面吸睛、女声拟人化解说
（本地 CosyVoice2，数字读法正确）、字幕与声音和画面逐页对应、光标指哪讲哪
（说的图=指的图）、讲图时局部放大、背景音乐不抢人声。画面主体是 pdf2ppt 的
论文总结 PPT——**先有讲清楚的 PPT 与旁白，再有视频**。
**生成不是终点**：出片后必须走迭代校对（阶段 6），六组门禁清零才算交付。

内容纪律与 pdf2ppt 一致：视频不新增任何数字或机制描述，全部来自 pdf2ppt 底稿。

## 多 agent 协同（编写与审核分离）

主对话（编排者）只做调度、跑脚本、核对门禁、迭代轮次管理；编写与审核是
**不同的 agent**（Agent 工具 general-purpose，审核 agent 不带编写者上下文）：

| 角色 | 职责 |
|---|---|
| 编排者（主对话） | 流程调度、跑脚本、汇总门禁、多轮迭代 |
| 底稿 agent | 调用 pdf2ppt 完成内容与旁白（其内部编排见 pdf2ppt SKILL） |
| 适配 agent | 按 adapt_notes.json 审读视频版旁白（衔接通顺、说人话、删句后补逻辑） |
| 封面 agent | 写 cover.json 吸睛文案 → make_cover 生成 → 视觉评审修改循环 |
| 视频制作 agent | 素材渲染、时间轴核对（含逐段指图核对）、渲染合成 |
| 审核 agent（独立） | 对照 timeline/srt/抽帧/试听样本做视频级评审，只报不改 |

三层校对串行（每层清零才进下一层）：
**① md/docx（paper-reader 门禁）→ ② PPT+旁白（pdf2ppt 三道门禁+审核）→
③ 视频（本技能六组门禁+审核）**。

## 工作流程

七阶段：0 环境 → 1 底稿（pdf2ppt，三层校对的第②层）→ 2 视频版适配 →
3 素材（封面/底图/TTS/BGM）→ 4 时间轴 → 5 渲染合成 → 6 迭代校对（**循环清零**）。

### 阶段 0：环境与目录

- `PY=<skill目录>/.venv/bin/python`，`S=<skill目录>/scripts`；`.venv` 缺失按
  requirements.txt 用 uv 重建（ffmpeg 由 imageio-ffmpeg 自带；CosyVoice2 走独立
  venv，见 TTS 一节）。TTS 需外网的仅 edge-tts 兜底路径。
- 模板配置固化在 `config.example.toml`，长期调整复制为 `config.toml`
  （.gitignore 忽略）；单次微调传 `--config`。
- 工作目录：PDF 所在目录建 `<PDF名>_video/`（结构同 pdf2ppt 版，另含
  content_video.json、adapt_notes.json、audio/、bgm/、cover/、slides/、
  timeline.json、subtitles.srt、segments/、frames/）。

### 阶段 1：底稿——pdf2ppt（先做好 PPT 与旁白，再做视频）

调用 pdf2ppt 技能产出 `<PDF名>_ppt/`（content.json + slide.pptx + 旁白）。
**其阶段 6 必须跑完**（三道门禁错误清零 + 审核 agent 通过）——底稿没讲清楚，
视频一定讲不清楚，这是硬前置。已有产物则复用：核验 content.json/pptx/旁白齐全，
并重跑 `check_narration.py`/`check_ppt.py` 确认按现行门禁仍然全绿（旧版产物
按现行规则补齐后再进视频）。

### 阶段 2：视频版内容适配（不动 pdf2ppt 产物）

```bash
$PY $S/adapt_content.py --content <PDF名>_ppt/content.json --out <工作目录>
```

机械适配：**删除「一.论文概述」页**（与视频封面卡内容重复，其旁白要点并入
封面开场白与下一页开场）、清空封面页 presenter/date/org（不做汇报人与单位
介绍）、旁白删除含网址/DOI 的句子（网址只放页面）。产物 content_video.json +
adapt_notes.json。

**适配 agent** 按 adapt_notes.json 审读改写旁白：衔接补顺（删页/删句后上下文
要通）、说人话（完整句子，禁电报式省略）、通读一遍。改完重跑
`adapt_content.py --check`（机械残留校验：网址/汇报人/单位提示词必须零残留）。

### 阶段 3：素材

1. **封面文案（封面 agent）**：读 content_video.json 的题目/总结页与红粗关键词，
   写 `cover.json`：`main_title`（≤16 字带钩子：疑问/反差/收益数字）、`highlight`
   （要高亮的子串，通常是关键数字）、`sub_title`（英文原题）、`tags`（2–3 个）、
   `source`（会议/期刊+年份，**不写汇报人与单位**）。
2. **底图与槽位**：`$PY $S/render_slides.py --pptx <PPT> --out <工作目录> --content content_video.json`
   ——4K 底图 + slots.json（标题/正文/图槽/总结区域几何 + 每张图的 ref/brief/axes
   元数据，指图一致性的依据）。
3. **解说 TTS**：`$PY $S/tts_narration.py --content content_video.json --out <工作目录>`
   ——逐页合成女声（数字读法归一化：342→三百四十二、40%→百分之四十、2.81×→
   二点八一倍，映射存 tts.json 溯源；词级时间戳驱动字幕）。
   **声音试听验收（必做）**：合成完抽取 2–3 段（含数字句、颜色句）交人耳试听，
   确认自然、清楚、无机器味；不满意先调后端参数，再考虑换后端。
4. **背景音乐**：`$PY $S/make_bgm.py --out <工作目录>/bgm/bgm.wav`（固定配方，
   每次同一首；config [bgm].path 可换自备音乐）。

### 阶段 4：时间轴（视频制作 agent 核对）

`$PY $S/build_timeline.py --content content_video.json --work <工作目录> --ppt-work <PDF名>_ppt`

自动完成：字幕按标点+jieba 词元切行（≤18 字）并用词级时间戳对齐；旁白按句聚成
讲解段、**分句级**细分光标目标；**指图用「图 N」编号精确匹配 figure.ref**，匹配
不上退回 brief/axes 词面、位置词兜底；光标落点解析"左上/右下"等位置词映射图内
九宫格；长讲图段（≥4s 且框能完整包含图区、不与其他区域相交）排局部放大。
**视频制作 agent 逐段核对 timeline**：说的图=指的图（segments[].fig 与旁白文本）、
光标终点是否落在真正描述的地方、不该放大的删 zooms。手工微调契约见
`references/timeline-spec.md`，改完只重渲染受影响页。

### 阶段 5：渲染合成

```bash
$PY $S/make_cover.py --content content_video.json --out <工作目录> \
        --extract <PDF名>_ppt --minutes <预计分钟数>
$PY $S/render_video.py --work <工作目录> --content content_video.json
```

渲染 20–60 分钟（30 分钟视频约 5 万帧），后台跑。调动画 `--render-only --only <idx>`；
调音量/字幕 `--assemble-only`。

### 阶段 6：迭代校对（循环，直到全部问题清零）

`$PY $S/check_video.py --work <工作目录> --content content_video.json --frames-out <工作目录>/frames`

六组门禁（**错误必须清零**，警告逐条确认）：

| 验证标准 | 门禁 |
|---|---|
| 1. 视频清晰 | 1080p/帧率/时长探针 + 抽帧拉普拉斯方差 ≥60 |
| 2. 旁白文字、声音、画面对应 | 字幕逐页还原旁白；抽帧与底图比对页序；字幕时间全落在对应页音频窗口 |
| 3. 解说女声、清楚、非AI味 | 主播音 ∈ 配置后端；人声响度达标无削波；**数字读法试听验收**（抽查数字句人耳确认，342 念三百四十二） |
| 4. 背景音轻松不抢解说 | 纯音乐间隙 ≤ 人声 -10dB 且 ≥-38dB（确实铺上了）；导出 bgm_sample.wav 人耳试听 |
| 5. 封面图文不重叠 | 封面存在 + 包围盒两两相交检测复检 |
| 6. **指图一致性** | 说图 N 的段：高亮框/放大框与图 N 的 bbox IoU ≥0.45——说的图≠指的图直接报错 |

外加：字幕无网址残留、动画生效抽帧、字幕可读性探针。

**审核 agent 视频级评审**（独立上下文）：Read 抽帧 → 视觉模型评审（封面/开场卡/
讲图页/放大帧，逐项问文字截断、图文重叠、光标是否停在所讲对象上）；听感无法
机器判定，交人工试听（voice_sample/bgm_sample + 成片抽查 2–3 段）。

修复入口（改完重跑对应阶段，绝不手改 mp4）：

- 字幕/断词/对应 → content_video.json 旁白 → tts（--only）→ build_timeline；
- 指错图/光标落点不准 → 手改 timeline.json segments[].fig 或 cursor 落点 → render --render-only；
- 数字读法错 → num_norm.py 规格表补规则 → tts 重合成该页；
- 音量/BGM 抢或不明显 → config [bgm] gain_db/duck_db → assemble-only；
- 清晰度 → 确认 4K 底图、crf ≤18；图模糊回 pdf2ppt 换图；
- 封面 → 改 cover.json → make_cover；声音不自然 → 换后端音色/参数。

**内容级自检**（审核 agent，逐条）：旁白说的图=指的图（逐段）；光标停在真正
描述的地方；旁白说人话（无「橙是X」式省略、句子完整）；数字读法正确；解说词
与画面节奏匹配；封面文案是否概括论文亮点。全过 + 门禁清零才交付。

## 交付物

1. `<论文题目>-video.mp4`：1080p 成片（封面开场 + PPT 讲解 + 女声解说 + 字幕 +
   背景音乐闪避）；
2. `<论文题目>-字幕.srt`：软字幕；`<论文题目>-cover.png`：封面；
3. `voice_sample*.wav` / `bgm_sample.wav`：人耳试听样本；
4. 对话里给出：视频大纲（页/时长/放大段）+ 渲染说明（后端与音色、数字读法
   归一化条数、封面文案依据、迭代轮次与每轮修了什么、试听验收结论）+ 遗留问题。

## 检查点清单

- [ ] 三层校对串行完成：paper-reader 门禁 → pdf2ppt 门禁+审核 → 本技能门禁+审核
- [ ] 多 agent 执行：封面/视频制作/审核为不同 agent，迭代 ≥1 轮
- [ ] 视频版适配完成：概述页已删、presenter/org 已清、旁白无网址句、--check 零残留
- [ ] cover.json 吸睛（≤16 字钩子+关键数字高亮），封面重叠检测通过
- [ ] TTS 全页成功；数字读法归一化（tts.json num_map 有记录）；**数字句试听通过**
- [ ] timeline 过目：逐段「说的图=指的图」；光标终点核对；放大段都是真讲图
- [ ] check_video 六组门禁 + 指图一致性 + 动画抽检全部通过，错误清零
- [ ] 审核 agent 视觉评审（封面/开场/讲图/放大帧）无缺陷；人工试听通过
- [ ] 交付三件命名正确；迭代轮次写进交付说明

## 硬性规则

1. 内容严格来自 pdf2ppt 底稿，视频不新增数字或机制描述。
2. 主播音用模板配置（默认本地 CosyVoice2 女声）；**数字读法必须归一化**（342 念
   三百四十二），每个数字都有 num_map 溯源；字幕/音量/封面同样走模板配置。
3. 字幕与声音由同一份时间戳驱动；**解说词不念网址**；旁白说人话（完整句子，
   禁电报式省略——「橙色区域是注意力层」不是「橙是注意力层」）。
4. **指图一致性硬规则**：旁白说的图与画面指的图必须一致（ref 驱动 + 门禁 IoU
   校验 + 审核 agent 逐段核对），这是三次强调的重点。
5. **迭代校对必做**：六组门禁 + 视觉评审 + 人耳试听全过才交付；修复只走
   「改 content_video.json / cover.json / timeline.json / config → 重跑脚本」。
6. 不修改 pdf2ppt 的产物与其 extract/；`<PDF名>_video/` 是唯一输出目录；
   视频版适配产物是 content_video.json，不回写 content.json。
