# 评审-修复自动迭代

自动循环执行"评审 → 修复"，直到评审报告中没有严重和高级别问题。评审与修复使用不同的模型服务，各自运行在独立的 Claude Code 进程中。

本目录不含 `SKILL.md`，不会被当作技能加载。评审与修复的具体规则不在这里复制，每次运行都直接调用 skills 目录中的 `code-review-doc`、`tdd-implement`、`techdoc` 技能，技能更新后自动生效。

## 使用

```bash
cp config.example.toml config.toml   # 首次使用，填写项目路径、需求标识、测试命令、两个模型的配置
chmod 600 config.toml
python3 loop.py --check              # 只做准备检查与两个模型的连通测试
python3 loop.py                      # 开始迭代
```

前提：目标项目是 Git 仓库，`docs/` 下已有 `request-<标识>-*.md`、`design-<标识>-*.md`、`implement-<标识>-*.md`，并且有未提交的代码改动。

## 每轮流程

1. 评审模型按 `prompts/review.md` 调用 `code-review-doc`，生成 `docs/review-<标识>-<日期>.md`。
2. 程序统计报告中"### 问题N（严重/高/中/低）"的标题数量。严重和高都为 0 时结束。
3. 修复模型按 `prompts/fixer.md` 调用 `tdd-implement` 阶段五，修复全部确认成立的问题并更新实施文档。
4. 程序自己执行 `test_cmd`，失败则停止。
5. 进入下一轮。通过后可按配置调用 `techdoc` 生成最终技术文档。

以下情况会停止并交给人处理：达到最大轮数；连续多轮严重与高级别问题数量不下降；评审过程改动了 `docs/` 以外的代码；修复进程输出 `LOOP_STATUS: NEED_HUMAN`；测试失败；模型调用失败或超时。

## 文件说明

| 文件 | 说明 |
|------|------|
| `loop.py` | 主程序，只依赖 Python 3.11 及以上的标准库 |
| `config.example.toml` | 配置样例，进入 Git |
| `config.toml` | 实际配置，含密钥，已被 `.gitignore` 忽略 |
| `prompts/review.md` | 发给评审模型的提示词 |
| `prompts/fixer.md` | 发给修复模型的提示词，最后一行必须输出 `LOOP_STATUS` |
| `prompts/techdoc.md` | 迭代通过后生成技术文档的提示词 |

提示词中可用的占位符：`{round}` 轮次、`{req_id}` 需求标识、`{project}` 项目路径、`{implement_file}` 实施文档、`{review_file}` 本轮评审文档（仅修复）、`{test_cmd}` 测试命令。提示词只写自动迭代需要补充的约束，评审和修复的规则请改对应技能。

## 模型配置

每个角色可以用 `settings_file` 引用已有的 Claude Code 配置文件（读取其中的 `env`），也可以直接写 `base_url`、`auth_token`、`model`、`[角色.env]`，直接写的值优先。

程序为每个角色生成独立的配置目录 `~/.cache/review-fix-loop/config/<角色>/`，通过 `CLAUDE_CONFIG_DIR` 指定给子进程，其中的 `skills` 软链接到本 skills 仓库。因此不会修改 `~/.claude/settings.json`，也不影响正在使用的交互会话。

## 运行记录

每次运行保存在 `~/.cache/review-fix-loop/runs/<项目>-<标识>/<时间>/`：

- `round-N/`：本轮评审报告副本、实施文档副本、`changes.patch`（修复后的完整未提交改动）、模型调用日志、测试日志
- `summary.md`：每轮各级问题数量与最终结论

## 注意

- 子进程使用 `bypassPermissions` 权限模式，模型执行命令不会再确认。请在单独的分支或工作树中运行，必要时用各轮的 `changes.patch` 回退。
- 修复模型不会提交代码，所有改动保持未提交状态。
