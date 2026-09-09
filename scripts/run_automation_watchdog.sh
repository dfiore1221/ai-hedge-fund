#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="/Users/davidfiore/Documents/Hedge Fund/current-ai-hedge-fund"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

cd "$PROJECT_ROOT"
mkdir -p reports/automation_watchdog

"$PYTHON" main.py automation-watchdog run >> reports/automation_watchdog/automation.log 2>&1
