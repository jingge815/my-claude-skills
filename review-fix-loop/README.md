# 评审-修复自动迭代

自动循环执行"评审 → 修复"，直到评审报告中没有严重和高级别问题。评审与修复可以分别使用 Kimi（账号登录）、DeepSeek（密钥登录，经 Claude Code）、Codex（账号登录，需外网），任意组合或互换，每次调用都是独立进程中的全新对话。

本目录不含 `SKILL.md`，不会被当作技能加载。评审与修复的具体规则不在这里复制，每次运行都直接调用 skills 目录中的 `code-review-doc`、`tdd-implement`、`techdoc` 技能，技能更新后自动生效。

## 使用

```bash
cp config.example.toml config.toml   # 首次使用，填写仓库、文档目录、需求标识、两个角色和提示词；真实 key 只写在这里
chmod 600 config.toml
python3 loop.py --check              # 只做准备检查与连通测试
python3 loop.py                      # 开始迭代
```

前提：

- `repos` 中每个都是 Git 仓库，至少一个有未提交的代码改动。
- `docs_dir` 下已有 `request-<标识>-*.md`、`design-<标识>-*.md`、`implement-<标识>-*.md`。
- kimi 与 codex 已在终端完成账号登录（`kimi login`、`codex login`），程序直接使用 `~/.kimi-code`、`~/.codex` 中的登录状态，不需要 key。DeepSeek 用密钥，写在 `config.toml` 或其引用的 `settings_file` 中。

## 每轮流程

1. 评审角色按评审提示词调用 `code-review-doc`，生成本轮的 `<docs_dir>/review-<标识>-<日期>-roundNN.md`。
2. 程序统计报告中"### 问题N（严重/高/中/低）"的标题数量。严重和高都为 0 时结束。
3. 修复角色按修复提示词调用 `tdd-implement` 阶段五，处理本轮评审文件并更新实施文档。
4. 进入下一轮。通过后可按配置调用 `techdoc` 生成最终技术文档。

以下情况会停止并交给人处理：达到最大轮数；连续多轮严重与高级别问题数量不下降；评审过程改动了任一仓库 docs 以外的代码；修复进程输出 `LOOP_STATUS: NEED_HUMAN`；模型调用失败或超时。

## 文件说明

| 文件 | 说明 |
|------|------|
| `loop.py` | 主程序，只依赖 Python 3.11 及以上的标准库 |
| `config.example.toml` | 配置样例，含三种 engine 的写法与组合示例，只有占位 key，进入 Git |
| `config.toml` | 实际配置，写真实路径与密钥，已被 `.gitignore` 忽略 |
| `prompts/review.md` | 默认评审提示词 |
| `prompts/fixer.md` | 默认修复提示词 |
| `prompts/techdoc.md` | 默认技术文档提示词 |

## 提示词

评审、修复、技术文档的提示词都可以自由书写，每个角色支持三个字段：

- `prompt = "path.md"`：提示词文件，相对本目录或绝对路径，默认是 `prompts/` 下的三个文件。
- `prompt_text = """..."""`：直接把完整提示词写在配置里，优先于 `prompt`。
- `extra_prompt = """..."""`：追加在上面提示词之后，只想多加几条要求时用这个，不用复制整份。

程序不执行测试命令。验证/测试命令由你写进修复提示词（通常放在 `[fixer].extra_prompt`），修复方执行并记录到实施文档，结果通过 `LOOP_STATUS` 反馈给程序。

程序依赖的约定不需要写进提示词，每次调用时会自动追加在最后：无人值守、评审报告必须写到 `{review_file}`、问题标题格式"### 问题N（级别）"、修复回复最后一行输出 `LOOP_STATUS`。

可用占位符：`{round}` 轮次号、`{req_id}` 需求标识、`{workdir}` 工作目录、`{repos}` 仓库列表（每行一个）、`{docs_dir}` 文档目录、`{implement_file}` 实施文档、`{review_file}` 本轮评审文件。

## 评审文件按轮次编号

每轮评审报告由程序指定文件名 `<docs_dir>/review-<req_id>-<YYYYMMDD>-roundNN.md`，例如 `review-temp-20261102-round01.md`、`review-temp-20261102-round02.md`。轮次号接着 `docs_dir` 中已有的最大轮次继续递增，重新运行不会覆盖之前的评审。修复角色只处理本轮的那一份。

## 多仓库

`[project]` 中用 `repos = [...]` 列出所有参与的仓库，`workdir` 是模型进程的工作目录（通常是这些仓库的公共父目录），`docs_dir` 是文档目录。程序会检查每个仓库的未提交改动，确认评审没有修改任何仓库的代码，每轮的 `changes.patch` 包含所有仓库的 diff。不在 `workdir` 下的仓库与文档目录会通过 `--add-dir` 交给模型。只有一个仓库时仍可以只写 `path = "..."`。

## 模型配置

每个角色用 `engine` 选择 CLI，评审与修复可以任意组合或互换：

| 模型 | engine | 登录方式 | 调用方式 |
|------|--------|----------|----------|
| Kimi | `kimi` | 账号登录（`kimi login`，状态在 `~/.kimi-code`） | `kimi -p --skills-dir <技能目录>` |
| DeepSeek | `claude` | 密钥登录：`base_url` + `auth_token`，或 `settings_file` 引用已有配置 | `claude -p --no-session-persistence`，独立 `CLAUDE_CONFIG_DIR`，不影响 `~/.claude` |
| Codex | `codex` | 账号登录（`codex login`，状态在 `~/.codex`），必须 `proxy = true` | `codex exec --ephemeral` |

`model` 可选，留空用 CLI 或服务端默认模型；`args` 可追加命令行参数；`[角色.env]` 追加环境变量。

## 技能目录

`[skills].dir` 指定 `code-review-doc`、`tdd-implement`、`techdoc` 所在目录，默认是本目录的上一级；单个角色可以用 `skills_dir` 覆盖。各 engine 的加载方式：

- claude：在独立配置目录下创建 `skills` 软链接指向该目录。
- kimi：通过 `--skills-dir` 加载，同时在提示词开头注明技能目录，要求先读 `<技能目录>/<技能名>/SKILL.md`。
- codex：在提示词开头注明技能目录，要求先读对应 `SKILL.md`。

连通测试（`--check`）的输出会显示每个角色使用的技能目录。

## 代理

角色设置 `proxy = true` 时，程序启动前在 bash 中执行 `[proxy].commands`（样例是 `clashon`、`clash mode global`、`clash select Ghelper '美国洛杉矶2'`），取得其中导出的 `http_proxy`、`https_proxy` 等变量，用 `check_url` 验证能访问外网，然后只把这些变量传给该角色的子进程，其他角色不走代理。`clash mode global` 与节点选择会改变整台机器上 clash 的状态，程序结束后不会自动恢复。

## 清空对话

每次评审、修复、技术文档都是新启动的 CLI 进程，不带任何历史对话，轮与轮之间只通过评审文档与实施文档传递信息。claude 用 `--no-session-persistence`、codex 用 `--ephemeral`，都不落盘；kimi 没有对应选项，程序在调用结束后删除 `~/.kimi-code/sessions/` 下本次会话目录及 `session_index.jsonl` 中对应的行（角色中设置 `clear_session = false` 可保留以便排查）。

## 运行记录

每次运行保存在 `~/.cache/review-fix-loop/runs/<工作目录名>-<标识>/<时间>/`：

- `round-NN/`：本轮评审报告副本、实施文档副本、`changes.patch`（修复后所有仓库的未提交改动）、模型调用日志（`*.log`）
- `summary.md`：每轮各级问题数量与最终结论

## 注意

- 子进程都以免确认模式运行（claude `bypassPermissions`、codex `--dangerously-bypass-approvals-and-sandbox`、kimi `-p`），模型执行命令不会再确认。请在单独的分支或工作树中运行，必要时用各轮的 `changes.patch` 回退。
- 修复角色不会提交代码，所有改动保持未提交状态。
