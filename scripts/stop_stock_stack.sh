#!/bin/zsh
set -euo pipefail

stop_pattern() {
  local pattern="$1"
  local pids
  pids="$(pgrep -f "$pattern" || true)"
  if [[ -n "$pids" ]]; then
    echo "$pids" | xargs kill
  fi
}

stop_pattern "portfolio_bot.py"
stop_pattern "streamlit run dashboard.py --server.port 8503"

osascript <<APPLESCRIPT
display notification "portfolio_bot 和 dashboard 已停止" with title "Stock Bot"
APPLESCRIPT
