---
name: dev-flow
description: 一键全流程主控。输入一段原始 prompt 与一份任务配置（tasks/<需求标识>.toml，样例见 tasks/example.toml），由 flow.py 确定性执行器在独立进程中自动串联：prompt 锐化 → 需求文档（requirement-doc）→ 技术设计（tech-design-doc；单模型默认 3 轮自评审修复，全局配置 ≥2 个多候选模型时自动切换 arena 多候选+裁判选一）→ TDD 编码（tdd-implement）→ 评审修复迭代（生成任务级配置调 review-fix-loop，默认 20 轮直到阻塞级清零）→ 技术文档（techdoc）。全程任务级 worktree、progress 工作进展日志、decisions.tsv 决策日志、心跳+看门狗监控、断点续跑；一个任务一份配置，多任务真正并行。触发词："全流程开发""一键从需求到交付""dev-flow""从头到尾把这个需求做完"。本技能主对话只做任务配置生成与确认、后台启动、状态监控与结果汇报，不亲自写文档、不亲自写代码。单阶段任务直接用对应技能，不必走本技能。
---

# dev-flow 全流程主控

把“一段原始需求描述”驱动成“可交付的代码与技术文档”。重活由 `flow.py` 确定性执行器完成：每个阶段都是独立进程中的全新对话，复用 `review-fix-loop/loop.py` 的引擎层与 `dev-flow/config.toml` 的全局角色；主对话只做配置、启动、监控、汇报。

## 一键式用法（主路径）

1. **生成任务配置**：从 `tasks/example.toml` 复制为 `tasks/<需求标识>.toml`，填入原始 prompt、仓库清单、核心目标（逐字）、验证命令、边界。真实路径只写在这个文件里（已被 .gitignore 忽略）。用户直接给出原始 prompt 时，主对话代为生成配置，其中 T3 级内容（核心目标、仓库、验收标准）向用户确认一次。
2. **校验启动**：`python3 dev-flow/flow.py -c <任务配置> --check`（不调模型）→ 通过后同一命令去掉 `--check` 后台运行。`--ping` 可先做角色连通测试。
3. **监控**：`python3 dev-flow/flow.py status <需求标识> --watch` 后台运行（或 Monitor/定时唤醒），按 `report_interval_minutes` 节奏向用户汇报当前阶段、各阶段状态、心跳与最新进展；发现 NEED_HUMAN、阶段失败或看门狗事故立即提醒。
4. **收尾**：全流程完成后汇报产物清单（request/design/implement/review-*/techdoc、progress、decisions.tsv、watchdog-report）与合并策略（worktree 合回主分支 / 保留待检查）；安全暂停时告知恢复命令 `--resume`。

其他入口：`--resume` 断点续跑（沿用 docs 产物与阶段完成状态）；`--stage S3` 只跑指定阶段（如单独补跑 `--stage S5` 技术文档）；`--no-watchdog` 不起看门狗。单阶段交互任务直接用对应技能，不进 flow.py。

## 阶段与门禁

| 阶段 | 执行 | 产出与门禁 |
|------|------|-----------|
| W0 worktree | flow.py 为每个仓建/复用 `worktrees/<需求标识>/<仓库名>/`（分支 `dev-flow/<需求标识>`）；`use_worktree=false` 时直连原仓 | worktree 就绪、基线记入 flow-state 与 decisions.tsv |
| S0 锐化 | sharpen 角色（pstack/sharpen 四问，内联在提示词） | 锐化稿存 run 目录；原始 prompt 永远原文保留 |
| S1 需求 | requirement 角色调 requirement-doc 技能 | G1：`request-<标识>-<日期>.md` 存在、含“核心目标”与“验收” |
| S2 设计 | 见下节两种模式 | G2：`design-<标识>-<日期>.md` 存在、含任务拆分；single 模式另要求每轮自评审记录 |
| S3 TDD 实施 | tdd 角色调 tdd-implement 技能（核心目标、仓库、验证命令、偏差分级全部写进提示词） | G3：实施文档存在、回复 FLOW_STATUS: OK |
| S4+S5 评审修复+交付 | flow.py 生成任务级 loop 配置（写入 run 目录，不动全局 config.toml），调 `loop.py -c`；核心目标、需求/设计文档绝对路径、验证纪律、边界自动注入 reviewer/fixer/techdoc 的 extra_prompt | G4：阻塞级（默认严重、高）清零、退出码 0；通过后 run_techdoc 自动生成技术文档 |

门禁失败或角色报告 `FLOW_STATUS: NEED_HUMAN`：安全暂停（状态落盘、进展日志记录恢复方式），不硬闯；用户处理后 `--resume` 继续。

## S2 设计阶段（两模式，重点保障设计质量）

- **模式判定**：任务配置 `[design].mode`（默认 auto）——auto 时全局 `config.toml` 的 `[[panel.arena]]` 条目 ≥2 走 arena，否则 single。
- **single（单模型）**：worker 写设计初稿 → **N 轮自评审修复**（默认 3，全新进程每轮）：重新仔细查看代码与调用链 → 围绕需求文档核心目标与相关文档逐条评审（代码契合、目标覆盖、DAG 可执行、验证可跑）→ **就地修改设计文档** → 在“### 自评审记录”追加该轮记录。程序核验每轮记录后才放行。
- **arena（多候选）**：各 `panel.arena` 候选并行独立写方案（同一需求契约）→ leader 裁判按 5 项标准评分选 base 并给移植清单 → 移植合成最终设计文档（“### 选型记录”注明 base/移植/拒绝）。候选掉队不拖垮整场，≥1 份即继续。
- `design_review` 角色默认与 design 同角色（自己改自己），可配置为 leader 交叉评审。

## 配置与状态文件

- **任务配置 `tasks/<需求标识>.toml`**：任务级参数唯一来源（原始 prompt、仓库、核心目标、验证命令、边界、轮数、超时、角色指派）。多任务并行 = 各一份配置 + 各一组 worktree + 各一个 run 目录，互不干扰；全局角色配置共享只读。
- **全局配置 `config.toml`**：只放厂商账号层角色（leader / worker.doc / worker.code / panel.arena / panel.interrogate）。
- **flow-state** `docs/flow-state-<标识>-<日期>.md`：当前快照（阶段表、仓库基线、恢复命令），每阶段边界更新。
- **progress 工作进展日志** `docs/progress-<标识>-<日期>.md`：append-only，每步一条（时间/阶段/步骤/进展/证据指针）；外部调用、门禁、每轮自评审、每轮评审结果、看门狗事故都落在这里。人随时可读，一眼看清执行到哪、证据在哪。
- **decisions.tsv**：决策轨迹（show-me-your-work 格式：ts/phase/decision/why/evidence/result），与 progress 分工——决策与理由记这里，步骤进展记 progress。
- **run 目录** `~/.cache/devflow/runs/<标识>/<时间>/`：心跳 heartbeat.json、stages.json、各阶段调用日志、生成的 loop-config.toml、看门狗报告。

## 监控与看门狗

- flow.py 每次外部调用与阶段流转都更新心跳；`status --watch` 供监控 Agent 或用户随时查看。
- **看门狗 `watchdog.py`**（独立进程，flow.py 启动时拉起）：心跳停滞（阈值=单次调用超时+30min，只告警不杀）、进程异常退出、总时长超限（终止进程树）→ 事故记入 `watchdog-incidents.tsv` 并同步 progress；结束时产出 `watchdog-report-<标识>.md`（事故时间线 + 面向流程系统的改进建议）并补一行 decisions.tsv。
- 主对话对产出负责：收尾时抽查关键产物（diff、验证结果、评审结论）与 progress/decisions 是否互相印证，不照单全收。

## 自主性分级（写进各阶段提示词，无人值守时）

| 级别 | 定义 | 处理 |
|------|------|------|
| T1 | 可逆实现细节（命名、等价写法、测试组织） | 直接做+记录 |
| T2 | 设计意图/接口/范围偏差 | 选最接近核心目标的可逆方案、显著标记、写入待确认事项 |
| T3 | 不可逆动作、产品偏好、验收标准变更 | 绝不执行，回报 NEED_HUMAN 安全暂停 |

## worktree 纪律

管理单位=任务：一个需求标识一组 worktree（每仓一个），子任务一律不建；S3 的并行单元 fan-out 由 tdd-implement 在任务 worktree 内按设计 DAG 自行调度，写冲突靠 DAG 共享文件检测。收尾只清理自己建的 worktree，删前必查未提交改动。

## 原则内联索引（pstack 纪律，不再单独复制）

证明它有效（对真实产物验证）；把工作切成本身可验证的单元；先消除共享再串行；守护上下文窗口（重活路由给外部进程，主对话只留结论）；可逆就不问人；造杠杆（任务配置 + flow.py 可复跑）；show-me-your-work（decisions.tsv 与 progress 双日志，证据是指针）；解释数字（benchmark-checklist 七问，性能支路 perf-optimize / hill-climb 单独触发后回 S4）。
