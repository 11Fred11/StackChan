#!/usr/bin/env bash
# Installs the gateway and voice service as systemd user services that start with your login.
# Logs: journalctl --user -u stackchan-gateway -f
set -euo pipefail
BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
for unit in stackchan-voice stackchan-gateway; do
  sed "s|@BACKEND@|$BACKEND|g" "$BACKEND/deploy/systemd/$unit.service" > "$UNIT_DIR/$unit.service"
done
systemctl --user daemon-reload
systemctl --user enable --now stackchan-voice.service stackchan-gateway.service
echo "Installed. To keep them running when you're logged out: loginctl enable-linger $USER"
