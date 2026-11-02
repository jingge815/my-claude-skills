#!/usr/bin/env bash
# 在技能目录下创建独立虚拟环境并安装固定版本依赖。重复执行是安全的。
set -euo pipefail
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$SKILL_DIR/.venv"

if [ ! -x "$VENV/bin/python" ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv -q "$VENV"
  else
    python3 -m venv "$VENV"
  fi
fi

if command -v uv >/dev/null 2>&1; then
  uv pip install -q --python "$VENV/bin/python" -r "$SKILL_DIR/requirements.txt"
else
  "$VENV/bin/python" -m pip install -q -r "$SKILL_DIR/requirements.txt"
fi
echo "paper-reader env ready: $VENV"
