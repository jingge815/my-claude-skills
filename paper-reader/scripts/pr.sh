#!/usr/bin/env bash
# 用法: scripts/pr.sh <脚本名(不带.py)> [参数...]
# 首次运行自动建环境，之后直接用技能自带 venv 执行 scripts/ 下的脚本。
set -euo pipefail
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$SKILL_DIR/.venv/bin/python"
if [ ! -x "$PY" ] || ! "$PY" -c "import pymupdf, docx, pptx, edge_tts, imageio_ffmpeg" 2>/dev/null; then
  bash "$SKILL_DIR/scripts/setup.sh" >&2
fi
name="$1"; shift
exec "$PY" -W ignore "$SKILL_DIR/scripts/${name%.py}.py" "$@"
