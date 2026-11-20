---
name: dev-flow
description: 一键全流程主控。输入一段原始 prompt，自动串联完成：prompt 锐化（pstack/sharpen）→ 需求文档（requirement-doc，复杂需求先 pstack/arena 多候选）→ 技术设计（tech-design-doc，强制双方案对比与任务依赖 DAG）→ TDD 实施（tdd-implement，任务级 worktree、独立单元可并行）→ 评审修复迭代（review-fix-loop / code-review-doc，高风险追加 pstack/interrogate 对抗评审）→ 技术文档（techdoc）。触发词："全流程开发""一键从需求到交付""dev-flow""从头到尾把这个需求做完"。本技能只做阶段路由、门禁判定、状态记录、决策日志与 worktree 管理，不亲自写文档、不亲自写代码。单阶段任务直接用对应技能，不必走本技能。
---

# dev-flow 全流程主控

把"一段原始需求描述"驱动成"可交付的代码与技术文档"，方式是把仓库里的单阶段技能按固定流水线串起来，并在每个阶段边界设门禁。主控自己的产出只有三样：任务 worktree、状态文件、决策日志——其余一切由对应技能产出。

## 模式

- **交互模式**（默认）：T2 级决策（见自主性分级）停下问用户。
- **--auto 全自动**：仅 T3 必停；可逆决策一律先做后报，门禁点集中汇报。
- **--resume <需求标识>**：读取既有 flow-state 文件，从断点阶段继续。
- 性能任务：主线走完（G4 通过）后，用户点名或需求文档含性能指标时，进入性能支路（perf-optimize 单点 / hill-climb 持续），结果回到质量闭环。

## 阶段路由表

| 阶段 | 调用 | 产出 | 门禁 |
|------|------|------|------|
| W0 worktree | 为主任务涉及的每个仓库建任务 worktree（见 worktree 规范） | `worktrees/<需求标识>/<仓库名>/` | worktree 就绪、基线 commit 记录 |
| S0 锐化 | Read `~/.claude/skills/pstack/sharpen/SKILL.md` 并遵循 | 锐化后的任务说明 | G0：锐化稿就绪（交互模式请用户确认一次；--auto 直接过） |
| S1 需求 | `requirement-doc`（复杂需求先 Read `pstack/arena/SKILL.md` 多候选：`python3 dev-flow/agents.py run arena:<name>` 并行候选、`run leader` 裁判） | `docs/request-<标识>-<日期>.md` | G1：文档存在、无阻塞级待确认、验收命令引用项目验证命令清单 |
| S2 设计 | `tech-design-doc`（含 architect 双方案；贵决策按需 arena） | `docs/design-<标识>-<日期>.md` | G2：含 ≥2 套候选形态对比、任务 DAG 无环、共享文件已标注串行、无阻塞级待确认 |
| S3 实施 | `tdd-implement`（并行单元 fan-out 子代理） | 代码改动 + `docs/implement-<标识>-<日期>.md` | G3：L0/L1 全绿、任务单元 100% 覆盖、实施文档已更新 |
| S4 质量闭环 | `review-fix-loop`（loop.py；或手工 `code-review-doc`↔`tdd-implement` 阶段五）；高风险追加 interrogate | `docs/review-<标识>-*.md`（及 interrogate 报告） | G4：阻塞级清零、（若开）interrogate 无确认项、全量回归通过 |
| S5 交付 | `techdoc` | 最终技术文档 | G5：文档产出、决策日志完整 |
| P 性能支路 | `perf-optimize` / `hill-climb` | 优化 commit + decision.tsv | 回到 S4 双闸评审 |

门禁失败的处理：回到上一阶段对应技能修复后重过门禁；连续 2 次同门禁失败或 loop.py 报 NEED_HUMAN 即安全暂停（见看门狗），不硬闯。

调用技能的方式：仓库根技能用 Skill 工具或 Read 其 SKILL.md 并遵循；`pstack/` 子文件夹技能一律 `Read ~/.claude/skills/pstack/<名>/SKILL.md` 并遵循；外部模型角色一律经 `python3 ~/.claude/skills/dev-flow/agents.py run <角色>`（角色见 `dev-flow/config.toml`）。

## 状态文件与决策日志

- **状态文件** `<docs_dir>/flow-state-<需求标识>-<日期>.md`，每阶段边界更新：当前阶段、各门禁状态（待/过/败+次数）、并行 worker 清单、worktree 路径、阻塞原因。人随时可读，一眼看清流水线在哪。
- **决策日志** `<docs_dir>/decisions.tsv`（`pstack/show-me-your-work` 格式：`ts/phase/decision/why/evidence/result`，append-only）：门禁通过/失败、T1/T2 偏差决策、arena 选型与落选理由、并行分配、回退、性能实验每轮结果（hill-climb 与 loop.py 自动写入，主控补记其余决策点）。techdoc 与实施文档的偏差说明直接引用此文件，不重复叙述。

## worktree 规范（任务级）

1. **管理单位 = 任务**（一个需求标识的完整流程）。启动时为本任务涉及的每个仓库各建一个 worktree：`<统一父目录>/worktrees/<需求标识>/<仓库名>/`；单仓库任务同样适用。
2. **子任务一律不建 worktree**。全流程（调研、实施、评审修复、loop.py 运行）都在任务 worktree 中；并行子代理直接在其中工作，写冲突唯一防线 = S2 产出的任务 DAG（文件不相交才并行、共享必串行）。
3. **多任务并行**：新对话、新需求标识 = 新的一组任务 worktree，互不干扰。
4. **收尾**：G5 通过或安全暂停后，向用户提示合并策略（合并回主分支 / 保留 worktree 待检查）；清理时只删自己建的 worktree，删前必查未提交改动。
5. flow-state.md 维护任务→各仓库 worktree 路径表，`--resume` 与清理以此为准。

## 自主性分级（何时打扰用户）

| 级别 | 定义 | 交互模式 | --auto |
|------|------|----------|--------|
| T1 | 可逆实现细节偏差（命名、等价写法、测试组织） | 直接做+记录 | 直接做+记录 |
| T2 | 设计意图/接口/范围偏差 | 停下问 | 选最近似的可逆方案执行、显著标记、门禁点集中追认 |
| T3 | 不可逆动作（force-push、删数据、部署）、产品偏好、验收标准变更、真死路 | 必停 | 必停 |

主运行中发现的可逆问题（坏技能、flaky 测试、工具故障）自行修复并单独记一行 decisions.tsv，不中断主目标；AI 自提的新需求写入待确认清单、门禁点集中请示，不自行开新的 dev-flow 流程。

## 并行规则

- S3 并行：按 S2 的任务 DAG，文件不相交的单元一条消息内 fan-out 多个 Agent 子代理（各自完整执行 tdd-implement 的 TDD 五步）；共享文件单元串行；主对话负责分配、汇总、每个合并点跑 L1 回归。
- S1/S2 多候选：`agents.py run arena:<name>` 后台并行（`[[panel.arena]]` 几条就几路），裁判用 `run leader`。
- S4 对抗评审：`review-fix-loop` 的 interrogate 阶段（`[loop].interrogate = true`）自动并行攻击者。
- 外部进程（agents.py 任务、L2 回归）启动后记录进 flow-state，完成即回收结论；不轮询，长任务用 `/loop` 或定时唤醒做心跳。

## 看门狗与安全暂停

- 停止谓词（任一触发即安全暂停）：同一门禁连续 2 次失败；loop.py 返回 NEED_HUMAN；外部角色调用失败或超时；T3 决策；用户中断。
- 安全暂停的动作：把当前状态与决策日志写完整（含"下次从这里继续"的具体位置），退出并告知用户恢复命令 `dev-flow --resume <需求标识>`。不带脏状态硬跑。
- 主控对每个子代理/外部角色的产出负责：审 diff、验证据（claims 必须有命令与输出支撑），不照单全收。

## 原则内联索引（本技能用到的纪律，出处为 pstack 原则，不再单独复制）

证明它有效（对真实产物验证，不接受"能编译"）；把工作切成本身可验证的单元（每单元以一次检查收尾）；先消除共享再串行（并行的前提是文件不相交）；守护上下文窗口（大输出路由给子代理，主对话只留结论）；可逆就不问人（见自主性分级）；造杠杆（可复跑的脚本优于一次性手工操作）；解释数字（性能数字一律经 benchmark-checklist 七问）。
