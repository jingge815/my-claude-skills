---
name: AICompilerDailyTracker
description: 当用户需要跟踪 AI 编译器领域的每日/近期学术进展，汇总指定时间区间（默认前一天）内国内外会议、期刊、arXiv 上发布的 AI 编译器相关论文，并生成"跟踪进展摘要（≤10 条）+ 参考文献 + 近 3 个月会议投稿提醒"的日报时使用本技能。触发场景包括"AI编译器日报""跟踪今天的AI编译器论文""汇总最近一周AI编译器进展""查一下 X 月 X 日到 X 日有哪些 AI 编译器论文""提醒我近期有哪些编译器相关会议截稿"等。数据源强制覆盖：arXiv + Semantic Scholar 检索脚本、国际期刊必查清单（TPDS/TOCS/TOPLAS/TACO/PACRO/TJSC 等）、中文核心期刊必查清单（软件学报/计算机学报/自动化学报/计算机研究与发展）、会议录用公示（Google/WebSearch）。本技能强制逐条核验论文出处（标题/机构/日期/网址必须与原文页面对照，禁止捏造），并强制发布前核查（时间符合性/主题符合性/检索覆盖完整性/文件格式/投稿提醒正确性，机械脚本+语义清单双重校验）通过后才交付。论文主题限定 AI 编译器及其扩展领域（AI infra、LLM 训推系统、KV cache、vLLM、存算一体 PIM、CGRA/FPGA 加速、MLIR/TVM/XLA、ML for Compiler、LLM for Code 等）。不用于单篇论文精读总结（paper-reader 的职责），也不用于泛主题学术调研报告（claude-skills-research 的职责）。
---

# AI 编译器每日科研进展跟踪（AICompilerDailyTracker）

对 AI 编译器领域做"每日/区间"级学术跟踪：按用户指定时间区间（默认前一天）检索 arXiv + Semantic Scholar + 会议/期刊官网（含中文核心期刊）新发布的论文，逐条读摘要、提炼"研究问题 + 方法"，输出不超过 10 条的进展摘要、可溯源参考文献，以及未来 3 个月的会议投稿提醒。产出报告文件 `ai-compiler-daily-<日期或区间>.md` 并在对话中完整呈现。

本技能是"可核验的信息搬运"，不是"内容创作"。每一条论文信息都必须来自真实抓取到的原文页面；检索不到就少写，绝不编造。

## 不可违背的原则

1. **时间区间是硬约束，判定口径是"首次公开日期"。** 每条论文取其**最早可公开获取日**（arXiv v1 提交日、期刊 online 日、会议论文集上线日中最早者）判定是否落在区间内：落在区间内 → 可进编号条目；论文集/期刊正式上线落在区间内、但首次公开早于区间（有更早的预印本或先行版本）→ 只进"区间观察"，并同时注明两个日期（首次公开日、正式上线日），**预印本公开超过 30 天的一律不得进编号条目**。每篇入选前必须查证是否存在更早的 arXiv 预印本（arXiv 网页版标题搜索）。（arXiv 公告滞后时的处理见"异常处理"）
2. **零捏造。** 每条必须附可访问网址；标题、作者、机构、日期、venue 必须与原文页面对照过才可写入。机构无法核验时只写第一作者姓名，不猜机构；venue 不确定时如实标注"出处待核验"或直接弃用该条。
3. **主题限定 AI 编译器（含扩展定义，见下节）。** 每条入选论文必须能说清它与"AI 编译/编译器关键技术/AI 系统 infra"的关系；关键词命中但主题无关的（如量子编译、生物信息学管线）必须剔除。
4. **每条必须真实读过摘要，摘要获取不限于官方渠道。** 获取优先级：① 脚本返回的 abstract（arXiv/S2）→ ② 官方摘要页（会议/期刊/DOI 页）→ ③ **按论文标题全网搜索**（WebSearch：作者主页、实验室页面、GitHub 项目页、ResearchGate、引用该文的其他论文、技术报道，任何页面只要能拿到完整摘要文本即可用；参考文献仍给正式出处链接）→ ④ 全部失败：该条降级到"区间观察"只写标题级事实。禁止只凭标题编造问题/方法；经渠道 ③ 获取摘要的条目在内部笔记记录来源，不必写进报告。
5. **投稿提醒必须现场核验。** 截止日期以会议官网当前 CFP 页为准（直连不可达的站点经 clash 代理访问，见"网络与代理"节；WikiCFP 只作线索不作依据）；核验不了的会议不进提醒列表。等级按 `references/venues.md` 标注（CORE 原表等级），CCF 等级仅在核验后并注给出。
6. **摘要不超过 10 条，多条时按优先级排序输出（匹配度 > 影响力 > 正式性）。** 排序规则依次比较：① **主题匹配度**——越贴近 AI 编译器核心（编译器/IR/代码生成/张量编译、MLIR/LLVM/TVM/XLA 等）越靠前，其次 AI infra/LLM 训推系统，再次软硬件协同，最后交叉方法；② **影响力**——知名机构/工业界实验室（如 Google、Meta、NVIDIA、Intel、清华、上交、MIT、ETH 等）、知名系统/开源项目的延续工作、知名学者团队、CCF/CORE 高等级 venue 优先；③ **正式性**——顶会/顶刊正式发表 > 期刊 > arXiv 预印本；④ 仍并列时按日期新者在前。被裁掉的第 11 条起在"区间观察"汇总点名。
7. **发布前必须过核查关。** 报告写完后必须执行机械校验脚本（`scripts/check_report.py`）+ 语义核查清单（阶段 6），全部通过才算交付；发现 FAIL 必须修复后重跑核查。未核查的报告不得输出给用户。

## 主题判定标准（入选/排除依据）

**入选（命中任意一组且确与 AI/编译器相关）：**

| 组别 | 关键词示例 |
|---|---|
| AI 编译器核心 | compiler/compilation、MLIR、LLVM、TVM、XLA、torch.compile/Inductor/Triton、tensor compiler/program、code generation、intermediate representation、autotuning/auto-tuning、polyhedral、loop/vectorization/parallelization、kernel fusion、e-graph/equality saturation、superoptimization、Halide |
| AI infra / LLM 系统 | AI/ML infrastructure、LLM inference/serving/training、vLLM、KV cache、PagedAttention、speculative decoding、tensor/pipeline parallelism、分布式训练、GPU kernel 优化与调度、LLM scheduling/mapping、inference engine、训练/推理集群 |
| 软硬件协同 | processing-in-memory/PIM/存算一体、CGRA/可重构阵列、FPGA/ASIC DNN/AI accelerator、tensor core、dataflow 架构、high-level synthesis、加速器映射/编译 |
| 交叉方法 | ML for compiler（learned cost model、编译优化自动调参）、LLM for code/compiler（编译反馈、并行化建议、IR 理解）、AI 编译基准测试 |

**排除**：与 AI 无关的传统编译/体系结构纯理论、量子编译器（不涉 AI workload）、仅把"pipeline/翻译"泛称 compiler 的非计算机领域、无系统组件的纯模型/算法改进。

## 标准工作流程

### 阶段 0：解析时间区间

- 从用户输入解析区间：`<开始日期> 到/至/- <结束日期>`、单日（"昨天/今天/2026-11-15"）、或相对表述（"最近一周/上周"）。
- **未指定时默认"前一天"**（本机时区的昨天）。
- 统一为 `START`、`END`（YYYY-MM-DD，含端点）。同时确定 `TODAY`（用于投稿提醒窗口 `[TODAY, TODAY+3个月]`）。
- 确定报告输出路径：当前工作目录下 `ai-compiler-daily-<START>.md`（单日）或 `ai-compiler-daily-<START>-<END>.md`（区间）。

### 阶段 1：arXiv + Semantic Scholar 双脚本检索（主数据源）

```bash
# ① arXiv（预印本主源，含完整摘要）
python3 /home/jgfeng/.claude/skills/AICompilerDailyTracker/scripts/arxiv_search.py \
  --start <START> --end <END> --out /tmp/arxiv_candidates.jsonl

# ② Semantic Scholar（期刊/会议正式论文补充源；服务端日期过滤实测失效，脚本本地硬校验兜底）
python3 /home/jgfeng/.claude/skills/AICompilerDailyTracker/scripts/s2_search.py \
  --start <START> --end <END> --max-seconds 240 --out /tmp/s2_candidates.jsonl
```

- 两个脚本输出 JSONL：arXiv 条目含 `arxiv_id/title/authors/published/abstract/categories/score/url`；S2 条目含 `title/venue/publicationDate/externalIds(DOI,DBLP,ArXiv)/abstract/score`。abstract 已是全文摘要，**这一步即完成"读摘要"**，无需再抓 abs 页。
- `score` 是关键词相关度预分，仅作排序辅助；**最终主题判定由你逐条阅读 title+abstract 完成**，按上节标准剔除无关项。
- S2 条目与 arXiv 重复（externalIds.ArXiv 相同）时合并为一条，出处优先标 arXiv；S2 条目有 venue/DOI 时标注"正式发表"并优先级靠前。
- 故障处理：arXiv 脚本部分组失败（exit=2）用 `--groups <组名>` 单独重跑；S2 无 key 限流严重（实测会持续 429），脚本已内置退避+熔断+全局时限（`--max-seconds`，默认 240s）：exit=0 带 `[warn]` 表示部分成功（用已有结果并在报告数据源说明中注明覆盖受限），exit=2 表示 S2 本次完全不可用——稍等重试一次，仍失败则跳过并继续，阶段 6 覆盖度核查相应标注"期刊正式论文覆盖依赖 dblp 巡检结果"；两个脚本全挂则改用 WebSearch 检索 `arxiv.org` 新论文。

**多轮迭代检索（防"没找全"，强制）：**

第一轮（阶段 1 两个脚本 + 阶段 2 官网巡检）完成后合并候选池去重。**若候选池 < 5 条，必须继续迭代检索，直到候选 ≥5 条或连续一轮无新增**，并在阶段 6 校对清单第 1 项记录轮次与结论：

- **第 2 轮（换词）**：S2 用 `--terms` 换扩展词表重跑，如 `memory hierarchy LLM;GPU kernel scheduling;neural code generation;model serving scheduler;FPGA AI accelerator;quantization inference;distributed training system;compiler autotuning;sparsity acceleration;inference offload`（可按当日线索自定，换词即可，不必全跑）；WebSearch 换不同关键词组合（中文/英文、系统名、机构名、`site:arxiv.org` + 月份）。
- **第 3 轮（换源）**：arXiv 换入口（`arxiv.org/search/?query=<词>` 网页版，与 API 限流独立）；dblp API `https://dblp.org/search/publ/api?q=<词>&format=json`；OpenAlex `https://api.openalex.org/works?filter=from_publication_date:<START>,to_publication_date:<END>&search=<词>`（无官方摘要但有机构，限流宽松）。
- 每轮结束重新计数；**连续一轮候选数无新增即判定"当日内容确实少"**，停止迭代，在报告数据源说明中如实写明"已迭代 N 轮检索，确认区间内符合主题的论文共 X 条"。禁止因候选少而放宽主题或时间标准充数。

### 阶段 2：期刊与会议官网巡检 + Google 检索（补充数据源，必查清单制）

按 `references/venues.md` 第六、七节执行**必查清单**，逐项记录巡检结果（"区间内有新论文 / 无 / 入口不可达"），供阶段 6 覆盖度核查使用：

1. **国际期刊必查**（TPDS、TOCS、TOPLAS、TACO、PACRO、TJSC、CL）：按 venues.md 每刊的"最新论文更新入口"巡检——dblp TOC / Springer Online First 可 curl；IEEE Xplore 页面 JS 渲染须用 WebFetch 工具；dl.acm.org 403 一律走 dblp。发现区间内新 online 且主题相关的论文，抓正式出处页（DOI/官网摘要页）拿到摘要后纳入候选。
2. **中文核心期刊必查**（软件学报 jos.org.cn、计算机学报 jsjkx.com、自动化学报 aas.net.cn、计算机研究与发展 crad.ict.ac.cn △）。中文期刊官网**无外网限制、服务端渲染可直接 curl 解析**，巡检三步法：① 抓"优先出版/最新录用"列表页（软件学报：首页 `/jos/home` 的"优先出版"区块，条目链接 `/jos/article/abstract/<id>`，详情页含 DOI/优先出版信息；自动化学报：`/cn/article/latest_all`，约 80 条带 DOI 链接 `//www.aas.net.cn/cn/article/doi/<doi>`）；② 按标题关键词粗筛相关条目（编译/并行/调度/加速/推理系统/大模型系统/存算一体/编译器等）；③ 仅对相关条目打开详情页，核对网络首发/录用日期是否落在区间内，并读摘要（中文摘要照常总结，主题标准不放宽）。jsjkx.com 为纯前端渲染、crad.ict.ac.cn 本网络不可达——两刊用 WebSearch `site:jsjkx.com 最新录用` / `site:crad.ict.ac.cn` 兜底。
3. **会议录用公示**：用 WebSearch（Google）检索，推荐检索式：
   - `<会议> <年份> accepted papers`（重点：MLSys、ASPLOS、PLDI、CGO、PPoPP、ISCA、MICRO、HPCA、SC、OSDI、SOSP、EuroSys、LCTES、DATE、ICCAD）
   - `<会议> <年份> calls for papers deadline`（为阶段 4 的截稿核验预热）
   - `site:arxiv.org <关键词> <月份>`（补漏）
   - `"<论文/系统名>" compiler|MLIR|vLLM news`（追大厂/名校系统新作）
   若公示日期落在区间内且含 AI 编译相关论文，从会议官网录用列表/论文页抓摘要后纳入候选。
4. 每个来源的巡检结论写入工作笔记（不进报告）：供阶段 6"覆盖完整性"核查，做到每刊每会"查过且有结论"。

### 阶段 3：筛选、提取机构与出处

对候选池（arXiv + S2 + 官网巡检）逐条确认后，补齐每条的"机构"与"出处"：

- **机构提取优先级**：① arXiv HTML 全文页 `https://arxiv.org/html/<id>`（含 Affiliation 字段，curl 带 UA 抓取）→ ② `curl -s https://arxiv.org/pdf/<id> -o /tmp/p.pdf && pdftotext -f 1 -l 1 /tmp/p.pdf -` 读首页作者块 → ③ WebSearch 论文标题+作者 → ④ 以上均失败：条目只写第一作者 et al.，不写机构。
- **优先级排序**按原则 6，选出最终 ≤10 条。
- 机构写法：第一机构即可（多个作者同校取一；校企联合取第一作者机构或并列写两家）。

### 阶段 4：投稿提醒核验（未来 3 个月）

- 窗口：`[TODAY, TODAY + 3 个月]`。
- 从 `references/venues.md` 第一/二/三节按相关度选候选会议（编译器与系统类优先），逐个访问官网 CFP/dates 页核验：是否存在落在窗口内的 Abstract/Full Paper 截止日。优先核验当年届官网（从系列页跳转）。
- 每条提醒 = 会议名 + 等级 + 截止日期 + 官网网址；官网未挂 CFP 或无法访问的会议直接跳过，不写"预计截稿"。
- 数量 3–8 条为宜，按截止日期先后排序。

### 阶段 5：输出报告

写入输出文件并在对话中完整呈现，**严格使用以下模板**（条目编号与引用标号一一对应）：

```markdown
# AI 编译器技术跟踪进展（{START} 至 {END}）

> 生成时间：{TODAY} ｜ 数据源：arXiv / Semantic Scholar / 会议与期刊官网（含中文核心期刊） ｜ 区间内入选论文：{N} 条

## 一、跟踪进展摘要

[1]{机构}的{第一作者}等人{在 arXiv 发布预印本 / 在 <venue> 发表论文}《{英文标题}》，针对{研究问题一句话}，提出{方法一句话}[1]。
[2]……

## 参考文献

[1] {作者列表前 3 位, et al.}. {标题}[arXiv preprint / C / J]. {venue 与编号或 arXiv ID}, {日期}. {网址}
[2]……

## 友情提醒（{TODAY} 起未来 3 个月内截稿的会议）

[1]{会议全名}[{等级}]，截至投稿时间 {YYYY-MM-DD}（Abstract {日期} / Full {日期}），{官网网址}；
[2]……

> 截止日期核验说明：以上均已于 {TODAY} 访问官网核验；未能核验的会议未列入。
```

- 摘要句式固定为"机构+作者+venue+论文标题+针对……问题，提出……方法+[n]"，每条 1–2 句。
- **分行规范（硬性）**：三个章节内的每个编号条目行（摘要/参考文献/提醒的 `[n]` 行）行尾必须加**两个空格**（Markdown 硬换行），条目之间不空行，保证渲染时逐条分行；"区间观察"的各段落之间空一行分段。机械校验脚本会检查此规范。
- 参考文献网址：arXiv 给 `https://arxiv.org/abs/<id>`，正式论文给官网/DOI/dblp 页。
- 末尾附一段 2–3 句的"区间观察"（如某方向集中出现、某会议公示了录用名单），只陈述本区间事实。

### 阶段 6：发布前核查（强制，不过不发）

报告文件写完后先跑机械校验，再做语义核查，两者全过才允许交付。

**机械校验（脚本）：**

```bash
python3 /home/jgfeng/.claude/skills/AICompilerDailyTracker/scripts/check_report.py \
  <报告路径> --start <START> --end <END> --today <TODAY>
```

脚本校验：三章节结构、条目编号连续且摘要↔参考文献一一对应、条数 ≤10、每条句式（书名号/针对/提出/尾部引用）、每条参考文献含网址、提醒含等级与日期且日期在 3 个月窗口内、URL 无残缺、标题区间日期一致。**有 FAIL 必须修复报告后重跑，直到 PASS。**

**语义核查清单（逐项自问；本清单可随使用经验持续补充新条目）：**

| # | 校对点 | 通过标准 |
|---|---|---|
| 1 | 是否找全 | 候选池 ≥5 条，或已按"多轮迭代检索"执行至少 2 轮换词/换源检索且连续一轮无新增；检索轮次、每轮关键词与结果数已记录；必查清单（国际期刊/中文期刊/会议公示）逐项有"查过且有结论"，漏查的先补查 |
| 2 | 是否符合主题 | 逐条重读：每条均命中主题判定标准且能说清与 AI 编译器的关联；关键词误命中的（量子/生物/纯模型改进等）已剔除或降级；多条时按优先级规则（匹配度>影响力>正式性）排序且顺序合理 |
| 3 | 是否时间满足 | 每篇论文的**首次公开日期**（arXiv v1 / 期刊 online / 论文集上线中最早者，须换算本地时区）确实落在 `[START, END]`；每篇都查证过有无更早预印本；有更早预印本（>区间）的只出现在区间观察且注明双日期；参考文献日期与摘要条目所述一致 |
| 4 | 论文是否真实存在 | 每条 DOI/网址实际访问或经 Crossref/OpenAlex 元数据核验过：标题、venue、日期、作者、机构与报告内容一致；机构是核验出来的而不是猜的 |
| 5 | 摘要未从官方渠道获取的条目，是否已在网上按关键字搜索 | 官方渠道（脚本/官网）拿不到摘要的每一条，都必须执行过"标题+关键词"的 WebSearch（作者主页/GitHub/ResearchGate/引用页等），并记录搜索行为与结果；搜到即用（原则 4 渠道 ③），确实全网无摘要才降级到区间观察 |
| 6 | 友情提醒是否正确、真实、存在 | 逐条三问：会议真实存在（官网可访问）？日期正确（与官网当前 CFP 页一致，Abstract/Full 区分清楚）？网址有效？任何一项不能现场核验的条目必须移除 |
| 7 | 输出格式符合 | 报告按命名规则落盘；模板三章节齐全；条目编号与引用一一对应（机械校验已覆盖）；对话中完整呈现 |
| 8 | 链接与引用质量 | 报告内所有网址可访问（至少核验过一次）；参考文献格式统一；机构/作者拼写与来源页一致 |

（清单预留扩展位：使用中发现新校对点，直接补入本表并在交付说明中注明。）

**迭代修复闭环（发现问题不许带病交付）：**

1. 执行机械校验 + 语义清单，记录全部问题；
2. 逐项修复（缺检索→回阶段 1/2 补搜；摘要缺失→按第 5 项补网上搜索；时间/主题不符→删或降级；提醒存疑→回官网复核或移除；格式问题→改报告）；
3. 修复完成后**重新执行完整校对**（机械脚本 + 8 项清单全量重跑，不是只查改动项）；
4. 循环直到零问题；迭代轮次计入交付说明（如"校对迭代 2 轮，全部通过"）。机械校验脚本因数据本身放行的场景（见异常处理）需在交付说明中留痕。

零问题后向用户交付报告全文，并附核查结论（机械校验 PASS + 8 项清单通过 + 校对迭代轮次 + 检索轮次）。

## 网络与代理（clash）

部分数据源直连不可达（WikiCFP、dl.acm.org 403、Google 系等），遇到 000/超时/403 时**先开 clash 代理重试，再降级备用入口**：

```bash
bash -ic 'clashon'    # 开启：必须用交互 shell 跑，直接敲 clashon 会因函数依赖缺失报错
curl -s -x http://127.0.0.1:7890 -o /dev/null -w '%{http_code}' https://www.google.com/   # 验证：返回 200 即可用
bash -ic 'clashoff'   # 关闭：报告交付后关闭，不留后台
```

- 机制：`clashon` 启动 clash 守护进程（监听 `127.0.0.1:7890`，跨 Bash 调用持续生效），但导出的代理环境变量**不跨调用持久**——后续请求必须显式带代理：`curl -x http://127.0.0.1:7890 <url>` 或 `http_proxy=http://127.0.0.1:7890 https_proxy=http://127.0.0.1:7890 <命令>`。
- 这一特性是优点：不带 `-x` 的请求仍直连，国内站点（jos.org.cn 等）巡检不受代理影响。
- 用途优先级：① 会议官网/CFP 直连失败时代理重试；② dl.acm.org 403 时经代理核验正式出处；③ WikiCFP 经代理可达，可作截稿线索（仍须官网确认）。
- 已验证经代理仍不可达的站点如实标注，不强求（如 crad.ict.ac.cn 代理前后均不可达）。
- arXiv API 与 Semantic Scholar 直连可用，一般无需走代理；如需：`http_proxy=http://127.0.0.1:7890 python3 <脚本>`。

## 异常处理

| 情形 | 处理 |
|---|---|
| arXiv 区间内 0 条，且 `END` 距今 <3 天 | 属 arXiv 公告/索引滞后 1–3 天的正常现象（API 索引新提交有延迟）。先用脚本跑一个近 7 天区间探测"最新可检索日"；若确认滞后，报告如实写明"指定区间暂无公告论文（arXiv 公告滞后）"，**不虚构、不用区间外论文充数**；对话中提示用户可改查最新公告日或稍后重跑 |
| S2 全部请求 429/失败 | 稍候重试一次；仍失败则在报告数据源说明中注明"S2 本次不可用"，覆盖度核查（阶段 6 第 3 项）相应标注"期刊正式论文覆盖依赖 dblp 巡检结果" |
| 官网/入口不可达（000/403） | 先按"网络与代理"节开 clash 代理重试；仍失败再按 venues.md 的 △ 标注换 dblp / conf.researchr.org / WebFetch 工具兜底；最终拿不到出处的论文弃用，并在覆盖度笔记中记录 |
| dblp 条目日期与官网 online 日期不一致 | 以官网（DOI 页/Xplore/Springer）为准；官网不可达时以 dblp 日期标注并注明"按 dblp 收录日" |
| 候选远超 10 条 | 按原则 6 优先级 + 相关度裁剪；被裁掉但确有价值的可在"区间观察"里一句带过（不带引用则不占编号） |
| 候选池 <5 条 | 触发"多轮迭代检索"：换词重跑 S2、WebSearch 换关键词组合、换检索源（arxiv 网页版/dblp API/OpenAlex）；连续一轮无新增才可判定"当日内容确实少"并如实写明检索轮次，禁止放宽主题/时间标准凑数 |
| 摘要官方渠道拿不到 | 按原则 4 渠道 ③ 执行"标题+关键词"WebSearch（作者主页/GitHub/ResearchGate/引用页），拿到即可用；确实全网无摘要的降级到区间观察，只写标题级事实 |
| 用户指定关键词聚焦（如"只要 MLIR 相关"） | 在主题判定标准上叠加用户过滤词，并在报告头部注明筛选口径 |
| 前一天恰好无任何符合条件论文 | 输出空摘要报告（模板照用，N=0），投稿提醒照常输出，并建议用户扩大区间 |
| 机械校验反复不过 | 逐条按 FAIL 信息修报告；若 FAIL 源自数据本身（如论文确无明确日期），说明原因后人工判定放行并留痕，不得为过校验而编数据 |

## 边界

- 单篇论文深度精读总结 → `paper-reader`；整篇 PPT 汇报 → `pdf2ppt`。
- 泛 AI/系统主题的学术调研报告（多来源综合分析、不限时间区间）→ `claude-skills-research`。
- 本技能只做"时间区间内的进展跟踪 + 投稿提醒"，不做文献综述、不做技术方案对比。
