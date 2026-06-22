#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/apple/Documents/Stock"
BOT_CMD="cd \"$PROJECT_DIR\" && python3.14 portfolio_bot.py"
DASHBOARD_CMD="cd \"$PROJECT_DIR\" && python3.14 -m streamlit run dashboard.py --server.port 8503"
DASHBOARD_URL="http://localhost:8503"
OPEN_D_HOST="127.0.0.1"
OPEN_D_PORT="11111"

is_port_open() {
  python3.14 - "$1" "$2" <<'PY'
import socket, sys
host = sys.argv[1]
port = int(sys.argv[2])
s = socket.socket()
s.settimeout(1)
try:
    s.connect((host, port))
    print("open")
except Exception:
    print("closed")
finally:
    s.close()
PY
}

is_process_running() {
  pgrep -f "$1" >/dev/null 2>&1
}

start_terminal_tab() {
  local cmd="$1"
  local escaped_cmd
  escaped_cmd="$(python3.14 - "$cmd" <<'PY'
import json, sys
print(json.dumps(sys.argv[1]))
PY
)"
  osascript <<APPLESCRIPT
tell application "Terminal"
    activate
    do script $escaped_cmd
end tell
APPLESCRIPT
}

OPEN_D_STATUS="$(is_port_open "$OPEN_D_HOST" "$OPEN_D_PORT")"
if ! is_process_running "streamlit run dashboard.py --server.port 8503"; then
  start_terminal_tab "$DASHBOARD_CMD"
fi

if [[ "$OPEN_D_STATUS" == "open" ]]; then
  if ! is_process_running "portfolio_bot.py"; then
    start_terminal_tab "$BOT_CMD"
  fi
else
  osascript <<APPLESCRIPT
display dialog "OpenD 当前没有监听 127.0.0.1:11111。\n已为你启动 dashboard，但暂时跳过 portfolio_bot。\n等 OpenD 起好后，再双击一次启动器即可。" buttons {"知道了"} default button "知道了" with title "Stock Bot 提醒"
APPLESCRIPT
fi

sleep 2
open "$DASHBOARD_URL"
