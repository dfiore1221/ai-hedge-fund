#!/bin/zsh
set -u

PROJECT_ROOT="${AIFUNDOS_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PYTHON="${AIFUNDOS_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
INTERVAL="${AIFUNDOS_WATCHDOG_INTERVAL_SECONDS:-900}"

if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

cd "$PROJECT_ROOT" || exit 1
mkdir -p reports/automation_watchdog

while true; do
  "$PYTHON" main.py automation-watchdog run >> reports/automation_watchdog/service.log 2>&1
  sleep "$INTERVAL"
done
