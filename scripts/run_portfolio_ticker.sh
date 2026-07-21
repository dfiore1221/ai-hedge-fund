#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:a:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
SOURCE="$PROJECT_ROOT/desktop_ticker/PortfolioTicker.swift"
BINARY="/tmp/aihf_portfolio_ticker"
export CLANG_MODULE_CACHE_PATH="/tmp/aihf_swift_module_cache"

if [[ ! -x "$BINARY" || "$SOURCE" -nt "$BINARY" ]]; then
  mkdir -p "$CLANG_MODULE_CACHE_PATH"
  swiftc "$SOURCE" -o "$BINARY" -framework AppKit
fi

"$BINARY"
