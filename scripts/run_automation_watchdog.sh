#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:a:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
PYTHON="$PROJECT_ROOT/.venv/bin/python"

cd "$PROJECT_ROOT"
mkdir -p reports/automation_watchdog

"$PYTHON" main.py automation-watchdog run >> reports/automation_watchdog/automation.log 2>&1
