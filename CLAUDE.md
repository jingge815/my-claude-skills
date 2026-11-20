# skills 仓库维护规则

1. **新增、修改或删除任何技能（含 `pstack/` 下的复制技能）时，必须同步更新 `README.md` 中对应条目与流水线总图；不同步视为改动未完成。**
2. `pstack/` 下的技能继续修改时保持适配规范：工具名用 Claude Code 原生（Agent / general-purpose / Explore / isolation: worktree），模型配置读 `dev-flow/config.toml`，不回引 pstack 插件路径；正文与技能名保持英文原样。
3. 新建技能遵循：单一职责、正文简短、可被 dev-flow 链式调用（中文技能用中文 description；`pstack/` 下技能保持英文 description）。
4. 密钥与真实路径只写在各 `config.toml` / 私有配置中（已被各目录 `.gitignore` 忽略），禁止进入本仓库。
5. worktree 纪律：一个任务（需求标识）一组任务级 worktree（每仓库一个，`worktrees/<需求标识>/<仓库名>/`）；子任务一律不建 worktree，并行子代理直接在任务 worktree 内工作，靠设计文档任务 DAG 的共享文件检测避免写冲突。
6. 硬规则表：由 `pstack/correct` 维护——每条规则配"由什么机制强制执行"（lint/脚本/测试/CI），文档只在判断题处使用。该技能每固化一条规则在此追加一行。
