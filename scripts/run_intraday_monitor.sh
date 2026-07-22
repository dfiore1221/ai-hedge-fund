#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="/Users/davidfiore/Documents/Hedge Fund/current-ai-hedge-fund"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

cd "$PROJECT_ROOT"
mkdir -p reports/intraday_monitor

day_of_week="$(date +%u)"
time_hhmm="$(date +%H%M)"

if [ "$day_of_week" -gt 5 ]; then
  exit 0
fi

if [ "$time_hhmm" -lt 0930 ] || [ "$time_hhmm" -gt 1605 ]; then
  exit 0
fi

"$PYTHON" main.py intraday-monitor now --apply-fills >> reports/intraday_monitor/automation.log 2>&1
