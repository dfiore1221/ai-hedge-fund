#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:a:h}
PROJECT_ROOT=${SCRIPT_DIR:h}

cd "$PROJECT_ROOT"
mkdir -p reports/weekly_review

if [[ -x "venv/bin/python" ]]; then
  PYTHON="venv/bin/python"
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
elif [[ -x "../.venv/bin/python" ]]; then
  PYTHON="../.venv/bin/python"
else
  PYTHON="python3"
fi

"$PYTHON" main.py weekly-review today >> reports/weekly_review/automation.log 2>&1
