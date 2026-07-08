#!/bin/zsh
set -euo pipefail

cd /Users/davidfiore/Desktop/ai-hedge-fund
mkdir -p reports/morning_brief

venv/bin/python main.py morning-email today >> reports/morning_brief/automation.log 2>&1
