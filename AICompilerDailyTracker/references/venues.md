# AI 编译器领域会议与期刊官方信息汇总

> **来源**：用户整理的《编译器会议-2024-05-07.xls》（含 2018–2024 各年度 sheet 与"大模型"专题 sheet），由本技能于 2026-11-16 逐条核验官方网址后汇总。
> **等级说明**：`等级` 列为原表中的 Core Rank（用户数据，原样保留）。原表未标注 CCF 等级；输出投稿提醒时可现场核验 CCF 等级后以 `CCF-X/CORE-X` 并注形式给出，未核验前只写 CORE 等级，不得臆造。
> **网址核验状态**：✓ = 2026-11-16 本机 curl 验证可达；△ = 该域名本网络不可达/受反爬限制，改用给出的备用入口；其余为系列稳定入口。会议官网多为逐年轮转域名（如 `ppopp26.sigarch.org`、`sc26.supercomputing.org`），下表优先给**系列稳定入口**，当年站点用"当前届官网规律"列推导或从系列页跳转。
> **运维注意**：本机网络下 `www.wikicfp.com`、`dl.acm.org`（403）、`www.sciencedirect.com`（403）直连不可抓取——可按 SKILL.md"网络与代理"节开 clash（`bash -ic 'clashon'`，端口 7890，请求显式加 `-x http://127.0.0.1:7890`）后访问 WikiCFP 与 ACM DL（实测 2026-11-16：google 200、wikicfp 302、crad 仍 502）。投稿截止信息一律以会议官网为准，WikiCFP 只作线索；`crad.ict.ac.cn` 代理前后均不可达，用 WebFetch/WebSearch 兜底。

## 一、编译器 / 编程语言核心会议

| 缩写 | 全称 | 等级(CORE) | 官方入口 | 当前届官网规律 / 备注 |
|---|---|---|---|---|
| PLDI | ACM SIGPLAN Conf. on Programming Language Design and Implementation | A | https://www.sigplan.org/Conferences/PLDI/ ✓ | 每年一届，`pldiXX.sigplan.org`；AI 编译最高优先级跟踪对象 |
| POPL | Symposium on Principles of Programming Languages | A | https://www.sigplan.org/Conferences/POPL/ ✓ | `poplXX.sigplan.org`；偏理论 |
| CC | International Conference on Compiler Construction | A | https://conf.researchr.org/series/cc ✓ | 官网逐年轮转；MLIR/LLVM 相关论文常发地 |
| CGO | International Symposium on Code Generation and Optimization | B | https://www.cgo.org/ ✓ | 需带浏览器 UA 访问；与 PPoPP/HPCA/CC 常同期共址 |
| OOPSLA | Object-Oriented Programming, Systems, Languages and Applications（SPLASH） | A | https://www.sigplan.org/Conferences/OOPSLA/ ✓ | 一年两轮投稿（Spring/Fall），`splashXX.sigplan.org` |
| ICFP | International Conference on Functional Programming | B | https://www.sigplan.org/Conferences/ICFP/ ✓ | `icfpXX.sigplan.org` |
| PEPM | ACM SIGPLAN Symposium on Partial Evaluation and Semantics-Based Program Manipulation | C | https://www.sigplan.org/Conferences/PEPM/ ✓ | 与 POPL 共址 |
| LCTES | ACM SIGPLAN/SIGBED Conf. on Languages, Compilers and Tools for Embedded Systems | B | https://conf.researchr.org/series/lctes ✓ | 每年与 PLDI 共址 |
| PACT | International Conference on Parallel Architectures and Compilation Techniques | B | △ 官网逐年轮转（`pactconference.org` 本网络不可达） | 备用：dblp https://dblp.org/db/conf/pact/index.html ✓ |
| SAS | International Static Analysis Symposium | B | https://www.staticanalysis.org/ ✓ | 偏程序分析 |
| VMCAI | Verification, Model Checking and Abstract Interpretation | B | △ 官网逐年轮转，与 POPL 共址 | 备用：dblp https://dblp.org/db/conf/vmcai/index.html |
| ESOP | European Symposium on Programming（ETAPS 成员） | A | https://www.etaps.org/ ✓ | 截稿信息看 ETAPS 总站 |
| LOPSTR | Int. Symposium on Logic-Based Program Synthesis and Transformation | C | https://conf.researchr.org/series/lopstr ✓ | |
| SCAM | IEEE Working Conf. on Source Code Analysis and Manipulation | C | https://conf.researchr.org/series/scam ✓ | |
| LCPC | Workshop on Languages and Compilers for Parallel Computing | 未评级 | dblp: https://dblp.org/db/conf/lcpc/index.html ✓ | 无固定官网 |

## 二、体系结构 / 高性能与系统会议（AI infra 主战场）

| 缩写 | 全称 | 等级(CORE) | 官方入口 | 当前届官网规律 / 备注 |
|---|---|---|---|---|
| ASPLOS | Architectural Support for Programming Languages and Operating Systems | A | https://www.asplos-conference.org/ ✓ | 一年多个投稿周期（spring/summer/fall），LLM 系统+编译+架构交叉论文最集中 |
| ISCA | International Symposium on Computer Architecture | A | https://www.iscaconf.org/ ✓ | `XXXX.iscaconf.org`；AI 加速器/PIM 顶会 |
| MICRO | International Symposium on Microarchitecture | A | https://www.microarch.org/ ✓ | `microXX.microarch.org` 或 `XXXX.microarch.org` |
| HPCA | International Symposium on High-Performance Computer Architecture | A | https://www.hpca-conf.org/ ✓ | `XXXX.hpca-conf.org` |
| SC | IEEE/ACM Supercomputing Conference（SCxy） | A | https://supercomputing.org/ ✓，当年站 `scXX.supercomputing.org` ✓ | HPC+AI 系统论文 |
| PPoPP | ACM SIGPLAN Symposium on Principles and Practice of Parallel Programming | A | https://ppopp.org/ ✓（跳转 sigarch.org/ppopp ✓） | 与 CGO/HPCA/CC 常共址 |
| OSDI | USENIX Symposium on Operating Systems Design and Implementation | A | https://www.usenix.org/conference/osdi26 ✓ | `usenix.org/conference/osdiXX`；一年两轮投稿 |
| SOSP | ACM Symposium on Operating Systems Principles（原表未列，扩展） | A | https://sigops.org/s/conferences/sosp/ ✓ | 单数年举办 |
| ATC | USENIX Annual Technical Conference | A | https://www.usenix.org/conference/atcXX ✓ | `usenix.org/conference/atcXX` |
| NSDI | USENIX Symposium on Networked Systems Design and Implementation（扩展） | A | `usenix.org/conference/nsdiXX` | LLM 集群网络/分布式训练论文常见 |
| EuroSys | European Conference on Computer Systems | B | https://eurosys.org/ ✓，当年站 `XXXX.eurosys.org` ✓ | 一年两轮投稿 |
| MLSys | Conference on Machine Learning and Systems | B+ | https://mlsys.org/ ✓ | AI 编译/推理系统论文核心阵地 |
| IPDPS | IEEE International Parallel and Distributed Processing Symposium | B | https://www.ipdps.org/ ✓ | |
| ICS | International Conference on Supercomputing | B | https://ics-conference.org/ ✓ | SIGARCH 主办 |
| SPAA | ACM Symposium on Parallelism in Algorithms and Architectures | B | https://spaa.acm.org/ ✓ | 与 STOC/FOCS 共址（ACM FCRC 周） |
| ICPP | International Conference on Parallel Processing | B | △ `icpp-conference.org` 本网络不可达 | 备用：dblp https://dblp.org/db/conf/icpp/index.html |
| Euro-Par | European Conference on Parallel and Distributed Computing | A(原表) | △ `euro-parconference.eu` 本网络不可达 | 备用：dblp https://dblp.org/db/conf/europar/index.html |
| HotOS | USENIX Workshop on Hot Topics in Operating Systems | B | `usenix.org/conference/hotosXX`（无年份后缀会 404） | |
| HiPEAC | Int. Conf. on High Performance and Embedded Architectures and Compilers | B | https://www.hipeac.org/events ✓ | **注意：HiPEAC 年会 2023 年后已停办**，现以 HiPEAC events/ACACES 夏令营形式延续 |
| ISPA | IEEE Int. Symp. on Parallel and Distributed Processing with Applications | C | ieeexplore / CFP 检索 | 优先级低 |
| HPCC / HPCS / ASAP / HIPC / NPC / Euro-PDP | 高性能计算类会议群 | C（Euro-PDP 未评级） | HIPC: http://www.hipc.org ✓；Euro-PDP: https://www.euromicro.org/ ✓（会议列表页） | 优先级低，仅在检索到强相关论文时引用 |

## 三、EDA / 嵌入式 / 实时系统会议（软硬件协同、存算一体）

| 缩写 | 全称 | 等级(CORE) | 官方入口 | 备注 |
|---|---|---|---|---|
| DATE | Design, Automation and Test in Europe | B | https://www.date-conference.com/ ✓ | HLS/编译相关 track |
| ASP-DAC | Asia and South Pacific Design Automation Conference | C | https://www.aspdac.com/ ✓ | |
| DAC | Design Automation Conference（扩展） | A | https://www.dac.com/ ✓ | HLS/加速器方向补充跟踪 |
| ICCAD | International Conference on Computer-Aided Design（扩展） | B | https://iccad.com/ ✓ | 一年两轮投稿 |
| CODES+ISSS | Int. Conf. on Hardware/Software Codesign and System Synthesis | B | http://esweek.org/ ✓ | ESWeek（嵌入式系统周）成员 |
| CASES | Compilers, Architectures and Synthesis for Embedded Systems | C | http://esweek.org/ ✓ | ESWeek 成员 |
| RTSS | IEEE Real-Time Systems Symposium | A | https://www.rtss.org/ ✓ | |
| RTAS | IEEE Real-Time and Embedded Technology and Applications Symposium | B | https://www.rtss.org/ ✓（RTAS 页挂靠 RTSS 站） | |

## 四、软件工程会议（AI4Code / LLM for Compiler 交叉）

| 缩写 | 全称 | 等级(CORE) | 官方入口 | 备注 |
|---|---|---|---|---|
| ICSE | International Conference on Software Engineering | A | https://conf.researchr.org/home/icse-2027 ✓（系列页 `conf.researchr.org/series/icse`） | 一年两轮投稿 |
| FSE/ESEC | ACM Joint European Software Engineering Conference and Symposium on the Foundations of Software Engineering | A | https://conf.researchr.org/series/fse ✓ | |
| ISSTA | International Symposium on Software Testing and Analysis | B(原表) | https://conf.researchr.org/series/issta ✓ | |
| ASE | International Conference on Automated Software Engineering（扩展） | A | https://conf.researchr.org/series/ase ✓ | |

## 五、AI 侧会议（扩展，非原表；LLM 训推系统/高效推理论文常见去向）

| 缩写 | 等级参考 | 官方入口 | 备注 |
|---|---|---|---|
| NeurIPS | A | https://neurips.cc/ ✓ | 有 Datasets & Benchmarks、高效推理相关 track |
| ICML | A | https://icml.cc/ ✓ | |
| ICLR | 未入 CCF | https://iclr.cc/ ✓ | 投稿见 OpenReview |

## 六、期刊（必查清单：巡检"最新录用入口"判断区间内是否有新论文）

| 缩写 | 全称 | 等级(CORE) | 官方入口 | 最新论文每日/连续更新入口（巡检用） |
|---|---|---|---|---|
| TPDS | IEEE Trans. on Parallel and Distributed Systems | A | https://www.computer.org/csdl/journal/tq | IEEE Xplore 最新卷+Early Access：`https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=71` ✓（注意：不带 www；页面 JS 渲染，curl 拿不到列表，须用 WebFetch 工具） |
| TOCS | ACM Trans. on Computer Systems | A | https://dl.acm.org/journal/tocs | ACM 403 不可直连 → dblp `https://dblp.org/db/journals/tocs/index.html` ✓（新 online 条目按日更新） |
| TOPLAS | ACM Trans. on Programming Languages and Systems | A | https://dl.acm.org/journal/toplas | ACM 403 → dblp `https://dblp.org/db/journals/toplas/index.html` ✓ |
| TACO | ACM Trans. on Architecture and Code Optimization | B | https://dl.acm.org/journal/taco | ACM 403 → dblp `https://dblp.org/db/journals/taco/index.html` ✓（编译器论文高发，重点巡检） |
| PACRO | Parallel Computing（Elsevier） | B | https://www.sciencedirect.com/journal/parallel-computing | Elsevier 403 → dblp `https://dblp.org/db/journals/pc/index.html` ✓ |
| TJSC | The Journal of Supercomputing | C | https://link.springer.com/journal/11227 ✓ | Online First（每日更新）：`https://link.springer.com/journal/11227/volumes-and-issues` ✓ |
| CL | Computer Languages, Systems and Structures（Elsevier） | C | dblp: `dblp.org/db/journals/cl/index.html` ✓ | dblp TOC |
| TC（扩展） | IEEE Trans. on Computers | - | computer.org | Xplore `RecentIssue.jsp?punumber=89`（WebFetch） |
| TCAD（扩展） | IEEE Trans. on Computer-Aided Design | - | computer.org | Xplore `RecentIssue.jsp?punumber=63`（WebFetch）；HLS/EDA 相关 |
| JPDC（扩展） | J. of Parallel and Distributed Computing | - | dblp `journals/jpdc` ✓ | dblp TOC |

**dblp 巡检口径**：dblp 期刊 TOC 页顶部的最新卷/Early Access 条目带日期，可判断"新 online 是否落在区间内"；dblp 收录有 1–7 天延迟，发现区间内疑似新条目时再回 WebSearch/官网核对正式 online 日期。

## 七、中文核心期刊（必查清单；官网无外网限制、服务端渲染可 curl，巡检"优先出版/最新录用"栏）

**巡检三步法（2026-11-16 实测可行）**：① 抓"优先出版"列表页 → ② 标题关键词粗筛（编译/并行/调度/加速/推理系统/大模型系统/存算一体等）→ ③ 相关条目打开详情页核对网络首发/录用日期是否在区间内并读摘要。

| 期刊 | 官方入口（已验证） | 优先出版/最新录用实测入口 | 备注 |
|---|---|---|---|
| 软件学报 | https://www.jos.org.cn/jos/home ✓（1.3MB 服务端渲染） | 首页"优先出版"区块直接解析；条目链接 `/jos/article/abstract/<id>`，详情页含 DOI（10.13328/j.cnki.jos.xxxx）与优先出版标记；当期目录 `/jos/article/issue/<年_月>` | CCF A 中文刊；AI 编译、并行与分布式、智能软件方向 |
| 自动化学报 | https://www.aas.net.cn/ ✓ | 优先发表列表 `https://www.aas.net.cn/cn/article/latest_all`（服务端渲染，约 80 条带 DOI 链接，列表页无日期）；详情页 `//www.aas.net.cn/cn/article/doi/10.16383/j.aas.<id>`，打开核对录用/网络首发日期 | 原表未列、补充；智能系统/软硬件协同相关 |
| 计算机学报 | https://www.jsjkx.com/ ✓（纯前端渲染，curl 拿不到列表） | 用 WebSearch `site:jsjkx.com 在线出版 OR 最新录用` 兜底 | |
| 计算机研究与发展 | http://crad.ict.ac.cn/ △ 本网络不可达 | WebSearch `site:crad.ict.ac.cn` 兜底 | △ 代理前后均不可达 |
| JCST（扩展，英文） | https://www.jcst.ict.ac.cn/ △ | Springer 在线 | |

中文期刊条目进入摘要时：机构写中文、论文标题保留中文原标题，网址给官方论文页（doi 或期刊站内链接）；日期无法确认的条目不纳入。

## 八、截稿月份历史规律速查（仅供预判，**输出前必须到官网核验当年确切日期**）

| 会议 | 历史常见截稿月份（Abstract/Full） | 会议时间 |
|---|---|---|
| ASPLOS | 多周期滚动：约 3–4 月、7–8 月、10–11 月 | 次年 4–5 月 |
| PLDI | 两轮：约 7 月、11 月（Rolling） | 次年 6 月 |
| CC / CGO / HPCA / PPoPP | 约 7–9 月（多同址联合截稿） | 次年 1–4 月 |
| ISCA | 约 11 月 | 次年 6 月 |
| MICRO | 约 4 月 | 当年 10–11 月 |
| SC（Technical Papers） | 约 4 月 | 当年 11 月 |
| EuroSys | 两轮：约 5 月、9 月 | 次年 4–5 月 |
| MLSys | 约 9–10 月 | 次年 5 月 |
| OSDI | 两轮：约 12 月–1 月 | 当年 7 月 |
| SOSP | 约 4 月（单数年办） | 当年 10 月 |
| ATC | 约 1 月 | 当年 7 月 |
| ICSE | 两轮：约 3 月、7–8 月 | 次年 4–5 月 |
| NeurIPS | 约 5 月 | 当年 12 月 |
| ICML | 约 1 月 | 当年 7 月 |
| ICLR | 约 9 月 | 次年 4–5 月 |
| DATE | 约 9–10 月 | 次年 3–4 月 |
| DAC | 约 11 月（第二轮约 4 月） | 次年 6 月 |
| ICCAD | 两轮：约 4 月、8 月 | 当年 10–11 月 |

## 九、稳定的检索与核验备用入口

- **dblp**（可达但限流较凶：频繁访问会返回 202 或反爬质询页"Making sure you're not a bot"，代理换 IP 也可能仍 202）：`https://dblp.org/db/conf/<会议缩写小写>/index.html`、`https://dblp.org/db/journals/<缩写小写>/index.html`、检索 API `https://dblp.org/search/publ/api?q=<关键词>&format=json`。用于：确认论文正式发表出处（会议/期刊+年份）、按期浏览期刊新录用、找会议当年官网线索。dblp 被限流时改用 OpenAlex/S2 补位。
- **OpenAlex**（无限流、无需 key，实测好用）：`https://api.openalex.org/works/doi:<DOI>?mailto=<任意邮箱>` 单篇查元数据（**作者机构**、venue、日期）；`https://api.openalex.org/works?search=<词>&filter=from_publication_date:<START>,to_publication_date:<END>` 按区间检索。摘要多数缺失（ACM/IEEE 不开放），但机构核验首选。
- **Semantic Scholar bulk search**：`https://api.semanticscholar.org/graph/v1/paper/search/bulk?query=<词>&fields=...&publicationDateOrJournalPublicationDate=<start>:<end>`（服务端日期过滤实测不生效，须本地按 publicationDate 硬校验），覆盖期刊/会议正式论文（含 venue、DOI、DBLP 键）；本技能提供脚本 `scripts/s2_search.py` 直接调用，注意 429 限流退避。
- **Google/WebSearch**：会议录用公示与官网核验的主要通道，检索式见 SKILL.md 阶段 2。
- **conf.researchr.org 系列页**：CC/CGO/PPoPP/ICSE/FSE/ISSTA/ASE/LCTES/LOPSTR/SCAM 等，含当年会议链接与日程、录用论文列表。
- **arXiv**：`https://arxiv.org/abs/<id>`（摘要页）、`https://arxiv.org/html/<id>`（LaTeXML 全文页，**含作者机构**）、`https://arxiv.org/pdf/<id>`（配合 pdftotext 提取首页机构）。
- **OpenReview**：`https://openreview.net/` 查 ICLR/NeurIPS 投稿与评审。
- **官网不可达时**：改用 dblp + WebSearch 交叉核验，报告中网址给 dblp/官方镜像链接，不得给未核验链接。
