#!/bin/bash
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

git submodule update --init --recursive

if [ -f requirements.txt ]; then
  pip install -r requirements.txt || echo "pip install failed, continuing"
elif [ -f pyproject.toml ]; then
  pip install -e . || echo "pip install -e failed, continuing"
else
  echo "No Python dependency file found, skipping"
fi

pip install ruff==0.16.2 black || echo "pip install ruff/black failed, continuing"
