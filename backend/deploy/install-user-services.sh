#!/usr/bin/env bash
# Installs the gateway and voice service as systemd user services that start with your login.
# Logs: journalctl --user -u stackchan-gateway -f
set -euo pipefail
BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
APPLIO="${APPLIO_DIR:-$HOME/Projects/Applio}"   # optional: RVC voice conversion
mkdir -p "$UNIT_DIR"
UNITS=(stackchan-voice stackchan-gateway)
[ -x "$APPLIO/.venv/bin/python" ] && UNITS+=(stackchan-rvc)
for unit in "${UNITS[@]}"; do
  sed -e "s|@BACKEND@|$BACKEND|g" -e "s|@APPLIO@|$APPLIO|g" "$BACKEND/deploy/systemd/$unit.service" > "$UNIT_DIR/$unit.service"
done
systemctl --user daemon-reload
systemctl --user enable --now "${UNITS[@]/%/.service}"
echo "Installed. To keep them running when you're logged out: loginctl enable-linger $USER"
