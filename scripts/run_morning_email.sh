#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:a:h}
PROJECT_ROOT=${SCRIPT_DIR:h}

cd "$PROJECT_ROOT"
mkdir -p reports/morning_brief

if [[ -x "venv/bin/python" ]]; then
  PYTHON="venv/bin/python"
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
elif [[ -x "../.venv/bin/python" ]]; then
  PYTHON="../.venv/bin/python"
else
  PYTHON="python3"
fi

"$PYTHON" main.py morning-email today >> reports/morning_brief/automation.log 2>&1
