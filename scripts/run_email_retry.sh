#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:a:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
PYTHON="$PROJECT_ROOT/.venv/bin/python"

cd "$PROJECT_ROOT"
mkdir -p reports/email_queue

time_hhmm="$(date +%H%M)"

if [ "$time_hhmm" -lt 0445 ] || [ "$time_hhmm" -gt 1800 ]; then
  exit 0
fi

"$PYTHON" main.py email-retry all >> reports/email_queue/automation.log 2>&1
