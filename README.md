# skills 仓库总览

个人 Claude Code 技能仓库：开发流水线技能链 + pstack 借用技能 + 性能优化技能 + 论文/写作类技能。维护规则见 [CLAUDE.md](CLAUDE.md)：**任何技能的新增/修改/删除，必须同步更新本 README 对应条目。**

## 开发流水线总图

```text
用户原始 prompt + 任务配置 tasks/<需求标识>.toml（一个输入）
    │
    ▼
dev-flow/flow.py 一键确定性执行器（门禁+心跳+状态+决策日志+worktree，每阶段独立进程）
    │
    ├─ W0 worktree   每仓一个任务 worktree（use_worktree=false 可直连原仓）
    ├─ S0 锐化        pstack/sharpen（内联提示词）
    ├─ S1 需求        requirement-doc
    ├─ S2 设计        tech-design-doc：single=单模型+3 轮自评审修复（默认）；
    │                 arena=多候选并行+裁判选一+移植合成（全局 panel.arena ≥2 自动启用）
    ├─ S3 实施        tdd-implement（核心目标/仓库/验证命令注入提示词；DAG 单元可并行）
    ├─ S4 质量闭环    生成任务级配置调 review-fix-loop = code-review-doc（合规闸）↔ tdd-implement
    │                 阶段五，默认 20 轮直到阻塞级清零；高风险追加 pstack/interrogate（对抗闸）
    ├─ S5 交付        techdoc（评审通过后 loop run_techdoc 自动生成；--stage S5 可单独补跑）
    │
    └─ P 性能支路     perf-optimize（单点）/ hill-climb（持续）→ 回 S4 双闸

全程：任务 worktree + flow-state 快照 + progress 工作进展日志（每步+证据）
      + decisions.tsv 决策日志 + 心跳/看门狗（watchdog.py：停滞/死亡/超限 → 事故报告+改进建议）
外部模型：flow.py / agents.py 复用 loop.py 引擎层（角色配置 dev-flow/config.toml：
      leader 主决策 + worker 干活 + panel 多候选；任务参数在 tasks/*.toml，多任务真并行）
```

## 技能总表

### 一、主流程文档链（仓库根）

| 技能 | 职责 | 典型触发 | 上下游 |
|------|------|----------|--------|
| requirement-doc | 原始描述→规范需求文档（入口先 sharpen 锐化；复杂需求可先 arena 多候选） | "把这个想法整理成需求文档" | 上游 dev-flow/sharpen/arena；下游 tech-design-doc |
| tech-design-doc | 需求文档→可落地设计（强制 ≥2 套候选形态对比；产出任务拆分与依赖 DAG） | "根据需求文档生成技术设计" | 上游 requirement-doc；下游 tdd-implement |
| tdd-implement | 设计→TDD 代码实施 + 评审问题修复（偏差自主性三级；代码量六条硬约束；昂贵测试例外通道；任务级 worktree；DAG 并行） | "按设计文档开始开发""按评审文档改一改" | 上游 tech-design-doc / code-review-doc；下游 code-review-doc |
| code-review-doc | 三份文档为基准的合规性评审（含代码量六条硬约束；只评审不改代码） | "评审一下这次改动" | 上游 tdd-implement；下游 tdd-implement 阶段五 |
| review-fix-loop | 评审↔修复自动迭代程序（loop.py，可配 interrogate 对抗阶段与 techdoc 收尾） | `python3 loop.py` | 上游 code-review-doc/tdd-implement；下游 techdoc |
| techdoc | 最终交付技术文档（只读代码不改代码） | "写一份最终技术文档" | 上游全流程 |

### 二、pstack 借用技能（`pstack/` 子文件夹，保留英文名与英文正文）

**引入方式**：`pstack/` 下的技能不会被 Claude Code 自动注册为斜杠技能（实测），由其他技能或主控**按路径调用**：`Read ~/.claude/skills/pstack/<名>/SKILL.md 并遵循`。

| 技能 | 职责 | 被谁调用 |
|------|------|----------|
| pstack/sharpen | prompt 深度化重写：更深而非更大（专家会做什么/最原始材料/方法工具/何为正确） | requirement-doc 入口、dev-flow S0 |
| pstack/architect | 编码前强制比较两套类型/签名/模块边界再择一 | tech-design-doc、perf-optimize |
| pstack/arena | N 候选并行→跨厂商裁判→选 base→融合落选者强项（候选与裁判来自 dev-flow/config.toml） | requirement-doc/tech-design-doc 按需、dev-flow S1/S2 |
| pstack/interrogate | 多模型对抗评审：每条 finding 必须附真实运行证据，只报不改 | review-fix-loop interrogate 阶段、code-review-doc 高风险建议 |
| pstack/swarm | 并行 fan-out→汇聚→单报告的通用原语（会话内 Agent 或外部 agents.py worker） | 需要并行覆盖/竞速/普查时 |
| pstack/how | "X 怎么工作"：只读子代理调研调用链与架构解释 | 需求/设计阶段调研 |
| pstack/why | "X 为什么这样"：从 git 史挖设计动机与约束（区分作者原话与推断） | tech-design-doc 现状基线 |
| pstack/benchmark-checklist | 性能数字七问核验（limiter/调优/极限/错误/复现/相关性/发生否） | perf-optimize、hill-climb |
| pstack/show-me-your-work | decisions.tsv 决策日志纪律（append-only，证据指针） | dev-flow、hill-climb、review-fix-loop |
| pstack/create-verification-skill | 为项目生成"像用户一样驱动应用"的验证技能（备用：项目无现成验证命令时用） | 按需 |
| pstack/correct | 把 agent 反复犯的错误固化成硬规则（架构>类型>lint>测试>文档） | "同样的问题又犯了"时 |

### 三、性能与主控（仓库根，中文）

| 技能 | 职责 | 典型触发 |
|------|------|----------|
| dev-flow | 一键全流程：任务配置 + flow.py 确定性执行器串联锐化→需求→设计（single 3 轮自评审 / arena 多候选）→TDD→评审迭代（默认 20 轮）→技术文档；任务级 worktree、progress 进展日志、decisions.tsv、心跳+看门狗、--resume/--stage | "全流程开发""一键从需求到交付""dev-flow" |
| perf-optimize | 单点性能问题：复现优先 + trace 定位 + 性能咒语（最便宜优先）+ 七问核验 | "修一下这个性能问题" |
| hill-climb | 持续指标优化：冻结 harness 证灵敏度 + 一次一改一测 + 保留或回退 + decision.tsv + 停止谓词 | "围绕这个指标持续优化" |

### 四、论文与写作类（仓库根，与开发流水线无关）

| 技能 | 职责 |
|------|------|
| paper-reader | 学术论文 PDF→中文总结（md+docx；九章正文+参考文献章逐条附原文 References 条目+附录；示例说明配流程图与数据排布图） |
| pdf2ppt | 学术论文 PDF→总结汇报 PPT + 逐页旁白 |
| proposalword2ppt | 项目申请书→答辩 PPT + 旁白稿 |
| grant-writing | 科研基金申请书全流程撰写（文献门槛：入库 >80 篇 CCF A/B/C 或软件学报/计算机学报/自动化学报/中国科学级别、优先近 5 年、逐篇关键点总结；三案竞标：多模型各出一案或单模型 3 并行子代理，高级模型五条标准选优定基准；成稿后九条验证点迭代评审修复，≤20 轮） |
| AICompilerDailyTracker | AI 编译器领域每日论文跟踪日报 |
| claude-skills-research | Claude Skills/Agent 技术主题学术调研报告 |
| book-to-skill | 书籍/文档→结构化 agent 技能 |
| format-replica-skill-gen | 从文档抽取排版范式生成格式复刻子技能 |

## 基础设施

### dev-flow/flow.py —— 一键全流程执行器

读任务配置驱动全流程（S4 生成任务级配置调 review-fix-loop，不动其全局 config.toml）：

```bash
python3 dev-flow/flow.py -c tasks/xxx.toml --check   # 校验配置/仓库/角色（不调模型）
python3 dev-flow/flow.py -c tasks/xxx.toml           # 一键全流程（后台运行）
python3 dev-flow/flow.py -c tasks/xxx.toml --resume  # 断点续跑；--stage S5 可单独补跑
python3 dev-flow/flow.py status <需求标识> --watch   # 实时状态（监控 Agent 用）
```

配套：`watchdog.py`（心跳停滞/进程死亡/总时长超限 → 事故记录 + watchdog-report 含流程改进建议）、`prompts/`（各阶段提示词模板，任务配置可覆盖）、`tasks/example.toml`（任务配置样例，真实路径只写在 tasks/ 下被 gitignore 的任务文件里）。

### dev-flow/agents.py —— 外部模型调度器

统一入口调用外部厂商模型（复用 review-fix-loop 的引擎层：claude/kimi/codex 的登录、settings 切厂商、会话清理）：

```bash
python3 dev-flow/agents.py list                    # 列出角色
python3 dev-flow/agents.py run leader -p "…"       # 运行主决策
python3 dev-flow/agents.py run arena:glm --prompt-file p.md --workdir <任务worktree>
python3 dev-flow/agents.py check leader arena:deepseek   # 连通测试
```

角色写法：`leader` / `doc` / `code` / `arena:<name 或序号>` / `interrogate:<name 或序号>`。需要 Python 3.11+（`/usr/bin/python3.12`）。

### dev-flow/config.toml —— 角色模型配置

模板 `config.example.toml` 入 git；实配 `config.toml` 已 gitignore（密钥在各 `--settings` 文件的 env 里，不进仓库）。角色三类：

- `[leader]` 主决策：分配任务、比较需求/设计候选、裁决、评审把关；
- `[worker.doc]` / `[worker.code]` 弱干活：写文档、写代码；
- `[[panel.arena]]` / `[[panel.interrogate]]` 多候选面板：几个条目并行几路。

**换厂商**：把角色条目的 `settings` 改指向对应配置文件（如 `~/.claude/settings.json.glm` / `.deepseek`）即可；**增减 arena 候选**：增删一个 `[[panel.arena]]` 块；裁判固定用 `[leader]` 不单配。proxy 需要时复用 `review-fix-loop/config.toml` 的 `[proxy]`。

### 验证命令约定

项目仓库在 CLAUDE.md（或 docs/verification.md）维护分层验证命令（L0 快速门禁 / L1 模块回归 / L2 全量含 slow），requirement-doc 验收标准、tdd-implement 回归门、interrogate 复现命令、hill-climb 冻结 harness 均引用该处，不各自复制。项目无现成验证命令且需要"像用户一样驱动应用"的验证时，用 pstack/create-verification-skill 生成。

## 快速上手

- **一键全流程**：说"dev-flow：<你的原始需求描述>"，主对话生成 `tasks/<需求标识>.toml`（T3 内容确认一次）→ `flow.py --check` 校验 → 后台跑 `flow.py` → `flow.py status <标识> --watch` 监控汇报；断点续跑 `--resume`，补跑某阶段 `--stage S3`。
- **单阶段**：按各技能触发语点名技能。
- **评审-修复迭代**：配置 `review-fix-loop/config.toml` 后 `python3 loop.py --check` 验证、`python3 loop.py` 运行；对抗评审开关 `[loop].interrogate = true`（攻击者来自 dev-flow/config.toml）。
