# skills 仓库总览

个人 Claude Code 技能仓库：开发流水线技能链 + pstack 借用技能 + 性能优化技能 + 论文/写作类技能。维护规则见 [CLAUDE.md](CLAUDE.md)：**任何技能的新增/修改/删除，必须同步更新本 README 对应条目。**

## 开发流水线总图

```text
用户原始 prompt（一个输入）
    │
    ▼
dev-flow 主控（路由+门禁+状态+决策日志+worktree，不亲自干活）
    │
    ├─ S0 锐化        pstack/sharpen
    ├─ S1 需求        requirement-doc（复杂需求先 pstack/arena 多候选）
    ├─ S2 设计        tech-design-doc（强制双方案 + 任务拆分 DAG；贵决策按需 pstack/arena）
    ├─ S3 实施        tdd-implement（任务级 worktree；DAG 独立单元可并行）
    ├─ S4 质量闭环    review-fix-loop = code-review-doc（合规闸）↔ tdd-implement 阶段五
    │                 高风险追加 pstack/interrogate（对抗闸，finding 必须运行证据）
    ├─ S5 交付        techdoc
    │
    └─ P 性能支路     perf-optimize（单点）/ hill-climb（持续）→ 回 S4 双闸

全程：任务 worktree（一任务一组，子任务不建）+ flow-state 状态文件 + decisions.tsv 决策日志
外部模型：dev-flow/agents.py（角色配置 dev-flow/config.toml：leader 主决策 + worker 干活 + panel 多候选）
```

## 技能总表

### 一、主流程文档链（仓库根）

| 技能 | 职责 | 典型触发 | 上下游 |
|------|------|----------|--------|
| requirement-doc | 原始描述→规范需求文档（入口先 sharpen 锐化；复杂需求可先 arena 多候选） | "把这个想法整理成需求文档" | 上游 dev-flow/sharpen/arena；下游 tech-design-doc |
| tech-design-doc | 需求文档→可落地设计（强制 ≥2 套候选形态对比；产出任务拆分与依赖 DAG） | "根据需求文档生成技术设计" | 上游 requirement-doc；下游 tdd-implement |
| tdd-implement | 设计→TDD 代码实施 + 评审问题修复（偏差自主性三级；昂贵测试例外通道；任务级 worktree；DAG 并行） | "按设计文档开始开发""按评审文档改一改" | 上游 tech-design-doc / code-review-doc；下游 code-review-doc |
| code-review-doc | 三份文档为基准的合规性评审（只评审不改代码） | "评审一下这次改动" | 上游 tdd-implement；下游 tdd-implement 阶段五 |
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
| dev-flow | 一键全流程主控：锐化→需求→设计→实施→评审迭代→交付；任务级 worktree、门禁 G0-G5、flow-state、decisions.tsv、看门狗、--auto/--resume | "全流程开发""一键从需求到交付" |
| perf-optimize | 单点性能问题：复现优先 + trace 定位 + 性能咒语（最便宜优先）+ 七问核验 | "修一下这个性能问题" |
| hill-climb | 持续指标优化：冻结 harness 证灵敏度 + 一次一改一测 + 保留或回退 + decision.tsv + 停止谓词 | "围绕这个指标持续优化" |

### 四、论文与写作类（仓库根，与开发流水线无关）

| 技能 | 职责 |
|------|------|
| paper-reader | 学术论文 PDF→中文总结（md+docx，九章结构） |
| pdf2ppt | 学术论文 PDF→总结汇报 PPT + 逐页旁白 |
| proposalword2ppt | 项目申请书→答辩 PPT + 旁白稿 |
| grant-writing | 科研基金申请书全流程撰写 |
| AICompilerDailyTracker | AI 编译器领域每日论文跟踪日报 |
| claude-skills-research | Claude Skills/Agent 技术主题学术调研报告 |
| book-to-skill | 书籍/文档→结构化 agent 技能 |
| format-replica-skill-gen | 从文档抽取排版范式生成格式复刻子技能 |

## 基础设施

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

- **一键全流程**：直接说"dev-flow：<你的原始需求描述>"（或"全流程开发…"），主控自动建 worktree、串全流程、每阶段过门禁；`--auto` 全自动、`--resume <标识>` 断点续跑。
- **单阶段**：按各技能触发语点名技能。
- **评审-修复迭代**：配置 `review-fix-loop/config.toml` 后 `python3 loop.py --check` 验证、`python3 loop.py` 运行；对抗评审开关 `[loop].interrogate = true`（攻击者来自 dev-flow/config.toml）。
