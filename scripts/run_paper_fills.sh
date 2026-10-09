#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:a:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
PYTHON="$PROJECT_ROOT/.venv/bin/python"

cd "$PROJECT_ROOT"
mkdir -p reports/paper_fills

day_of_week="$(date +%u)"
time_hhmm="$(date +%H%M)"

if [ "$day_of_week" -gt 5 ]; then
  exit 0
fi

if [ "$time_hhmm" -lt 0930 ] || [ "$time_hhmm" -gt 1605 ]; then
  exit 0
fi

"$PYTHON" main.py fills apply >> reports/paper_fills/automation.log 2>&1
