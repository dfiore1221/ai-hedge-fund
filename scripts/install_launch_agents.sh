#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:a:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
SOURCE_DIR="$PROJECT_ROOT/automation"
TARGET_DIR="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
LOG_DIR="$HOME/Library/Logs/AIFundOS"
APP_DIR="$HOME/Applications/AIFundOS Scheduler.app"
APP_EXECUTABLE="$APP_DIR/Contents/MacOS/aifundos-scheduler"

mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$LOG_DIR"
if [[ ! -x "$APP_EXECUTABLE" \
  || "$PROJECT_ROOT/desktop_scheduler/AIFundOSScheduler.swift" -nt "$APP_EXECUTABLE" \
  || "$PROJECT_ROOT/desktop_scheduler/Info.plist" -nt "$APP_EXECUTABLE" ]]; then
  cp "$PROJECT_ROOT/desktop_scheduler/Info.plist" "$APP_DIR/Contents/Info.plist"
  swiftc "$PROJECT_ROOT/desktop_scheduler/AIFundOSScheduler.swift" -o "$APP_EXECUTABLE"
  codesign --force --deep --sign - "$APP_DIR" >/dev/null
fi

mkdir -p "$TARGET_DIR"

labels=(
  com.dfiore.ai-hedge-fund.morning-brief
  com.dfiore.ai-hedge-fund.email-retry
  com.dfiore.ai-hedge-fund.daily-setup-review
  com.dfiore.ai-hedge-fund.weekly-review
  com.dfiore.ai-hedge-fund.automation-watchdog
  com.dfiore.ai-hedge-fund.execution-lane
  com.dfiore.ai-hedge-fund.portfolio-ticker
)

for label in $labels; do
  source_plist="$SOURCE_DIR/$label.plist"
  target_plist="$TARGET_DIR/$label.plist"
  cp "$source_plist" "$target_plist"
  launchctl bootout "$DOMAIN/$label" >/dev/null 2>&1 || true
  launchctl bootstrap "$DOMAIN" "$target_plist"
done

launchctl kickstart -k "$DOMAIN/com.dfiore.ai-hedge-fund.automation-watchdog"
launchctl kickstart -k "$DOMAIN/com.dfiore.ai-hedge-fund.execution-lane"
launchctl kickstart -k "$DOMAIN/com.dfiore.ai-hedge-fund.email-retry"

echo "Installed and reloaded ${#labels[@]} AIFundOS launch agents."
echo "Scheduler app: $APP_DIR"
