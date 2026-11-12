# 评审-修复自动迭代

自动循环执行"评审 → 修复"。评审发现的问题都会修复；阻塞级别（默认严重、高）清零后，本轮修完剩余问题就结束，不再开新一轮评审。评审与修复可以分别使用 Kimi（账号登录）、DeepSeek（密钥登录，经 Claude Code）、Codex（账号登录，需外网），任意组合或互换，每次调用都是独立进程中的全新对话。

本目录不含 `SKILL.md`，不会被当作技能加载。评审与修复的具体规则不在这里复制，每次运行都直接调用 skills 目录中的 `code-review-doc`、`tdd-implement`、`techdoc` 技能，技能更新后自动生效。

## 使用

```bash
cp config.example.toml config.toml   # 首次使用，填写仓库、文档目录、需求标识、两个角色和提示词；真实 key 只写在这里
chmod 600 config.toml
python3 loop.py --check              # 只做准备检查与连通测试
python3 loop.py                      # 开始迭代
```

需要 Python 3.11 及以上。若 PATH 中的 `python3` 较旧（例如 PyTorch 环境为 3.10），会因缺少 `tomllib` 启动失败，改用系统的新版本即可，如 `/usr/bin/python3.12 loop.py`。

前提：

- `repos` 中每个都是 Git 仓库，至少一个在评审范围内有代码改动：默认是未提交改动；配置了 `base`/`head` 时是 `base..head` 的已提交区间，加上当前工作区未提交改动。
- `docs_dir` 下已有 `request-<标识>-*.md`、`design-<标识>-*.md`、`implement-<标识>-*.md`。
- kimi 与 codex 已在终端完成账号登录（`kimi login`、`codex login`），程序直接使用 `~/.kimi-code`、`~/.codex` 中的登录状态，不需要 key。DeepSeek 用密钥，写在 `config.toml` 或其引用的 `settings_file` 中。

## 每轮流程

1. 评审角色按评审提示词调用 `code-review-doc`，生成本轮的 `<docs_dir>/review-<标识>-<日期>-roundNN.md`。
2. 程序统计报告中"### 问题N（严重/高/中/低）"的标题数量。没有任何问题时直接结束。
3. 修复角色按修复提示词调用 `tdd-implement` 阶段五，处理本轮评审文件中的全部问题并更新实施文档。
4. 本轮有 `block_levels`（默认严重、高）中的问题时进入下一轮评审；只剩中、低级别时，修完即结束。通过后可按配置调用 `techdoc` 生成最终技术文档。

以下情况会停止并交给人处理：达到最大轮数；连续多轮阻塞级别问题数量不下降；评审过程改动了任一仓库 docs 以外的代码；修复进程输出 `LOOP_STATUS: NEED_HUMAN`；模型调用失败或超时。

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

可用占位符：`{round}` 轮次号、`{req_id}` 需求标识、`{workdir}` 工作目录、`{repos}` 仓库列表（每行一个）、`{review_scope}` 每个仓库的评审范围（已提交区间或未提交改动）、`{docs_dir}` 文档目录、`{implement_file}` 实施文档、`{review_file}` 本轮评审文件。

## 深度思考与根因修复（默认行为）

- 程序对 kimi 角色默认注入 `KIMI_MODEL_THINKING_EFFORT=max`，对 claude 角色默认注入 `CLAUDE_CODE_EFFORT_LEVEL=max`，让评审与修复都使用各自模型的最高思考强度；在角色的 `env` 中显式赋值可覆盖。
- `prompts/review.md` 要求评审先定位根因、给出能一次性收敛同类问题的修改思路，并在写入报告前用相反假设复核；还要抽查前几轮修复是否只在探针上生效。
- `prompts/fixer.md` 要求修复前先做根因分析、grep 全仓找同类入口与旁路，一次性修完并补测试；同时复核前几轮同类问题是否真正收敛，复发的按遗留问题一并修复。
- 推荐搭配：评审 `model = "kimi-code/k3.1-pro"`，修复 `model = "deepseek-flash[1m]"`。

## 评审文件按轮次编号

每轮评审报告由程序指定文件名 `<docs_dir>/review-<req_id>-<YYYYMMDD>-roundNN.md`，例如 `review-temp-20261102-round01.md`、`review-temp-20261102-round02.md`。轮次号接着 `docs_dir` 中已有的最大轮次继续递增，重新运行不会覆盖之前的评审。修复角色只处理本轮的那一份。

## 多仓库

`[project]` 中用 `repos = [...]` 列出所有参与的仓库，`workdir` 是模型进程的工作目录（通常是这些仓库的公共父目录），`docs_dir` 是文档目录。程序会检查每个仓库的评审范围，确认评审没有修改任何仓库的代码，每轮的 `changes.patch` 包含所有仓库评审范围内的 diff。不在 `workdir` 下的仓库与文档目录会通过 `--add-dir` 交给模型。只有一个仓库时仍可以只写 `path = "..."`。

### 评审已提交的提交区间

`repos` 的每个元素可以是路径字符串（只评审未提交改动），也可以是表，指定该仓库的提交区间：

```toml
repos = [
  { path = "/path/to/repo-a", base = "263fcda", head = "daef534" },
  "/path/to/repo-b",
]
```

- `base`：区间起点。不写则沿用旧行为，只评审未提交改动。
- `head`：区间终点，默认 `HEAD`。必须是当前检出 `HEAD` 的祖先（或等于 HEAD），否则程序拒绝启动——修复必须落在当前工作区，评审终点不能是工作区里不存在的提交。需要评审其他分支时，先建 worktree 或在对应分支运行。
- `head` 指向本轮要评审的最后一个提交；之后又提交了新改动时，要把 `head` 前移到新的提交。不写 `head` 则用 `HEAD`，会包含 `base` 到 `HEAD` 的全部差异（含中间合并带入的其他分支改动）。
- `head` 早于当前 `HEAD` 时，区间内的文件可能已被后续提交改动；评审报告里的行号要以当前工作区为准，`changes.patch` 里的提交区间 diff 也不能直接套到当前工作区。
- 每个仓库独立配置，可以有的看提交区间、有的只看未提交改动。
- 评审范围 = `git diff base..head` 的已提交区间 + `git diff HEAD` 的当前工作区未提交改动；修复角色只产生未提交改动，不会 commit。第二轮起评审自然能看到上一轮的修复。
- 评审过程改动代码的检测（fingerprint）同时覆盖提交区间、工作区未提交改动、未跟踪文件和当前 HEAD；评审若自己 commit 也会被发现。

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

- `round-NN/`：本轮评审报告副本、实施文档副本、`changes.patch`（修复后所有仓库评审范围内的改动，含已提交区间与未提交改动）、模型调用日志（`*.log`）
- `summary.md`：每轮各级问题数量与最终结论

## 注意

- 子进程都以免确认模式运行（claude `bypassPermissions`、codex `--dangerously-bypass-approvals-and-sandbox`、kimi `-p`），模型执行命令不会再确认。请在单独的分支或工作树中运行，必要时用各轮的 `changes.patch` 回退。
- 修复角色不会提交代码，修复改动保持未提交状态；配置了 `base` 时，评审范围里已提交的改动不会被改写。
