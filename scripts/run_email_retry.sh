#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="/Users/davidfiore/Documents/Hedge Fund/current-ai-hedge-fund"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

cd "$PROJECT_ROOT"
mkdir -p reports/email_queue

time_hhmm="$(date +%H%M)"

if [ "$time_hhmm" -lt 0450 ] || [ "$time_hhmm" -gt 1200 ]; then
  exit 0
fi

"$PYTHON" main.py email-retry morning >> reports/email_queue/automation.log 2>&1
