#!/usr/bin/env bash
# Deploy jobsearch-buddy on the box that runs it (devbox): pull main, refresh
# the venv, install the systemd --user units, restart the MCP daemon.
# Self-contained on purpose: these are free-wheeling dev boxes, not IaC-managed
# hosts. Idempotent — re-run any time.
#
#   on devbox:           deploy/deploy.sh
#   from any other box:  ssh devbox '~/projects/jobsearch-buddy/deploy/deploy.sh'
#
# (Tailscale SSH — keyless, works headless from cron/agents.)
#
# First-time provisioning, not managed here:
#   - ~/.config/jsb-mcp.env  (secrets — from 1Password)
#   - loginctl enable-linger $USER  (units survive without a login session)
set -euo pipefail

main() {
  local repo unit_dir
  repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  unit_dir="$HOME/.config/systemd/user"
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null || { echo "uv not found (expected ~/.local/bin/uv)"; exit 1; }

  echo "== pull =="
  git -C "$repo" pull --ff-only

  echo "== venv (uv sync) =="
  (cd "$repo" && uv sync)

  echo "== units =="
  mkdir -p "$unit_dir"
  install -m 0644 "$repo/deploy/jsb-mcp.service" \
                  "$repo/deploy/jsb-sync.service" \
                  "$repo/deploy/jsb-sync.timer" "$unit_dir/"
  systemctl --user daemon-reload
  # jsb-sync.service is static — the timer drives it; never enable it directly.
  systemctl --user enable jsb-mcp.service jsb-sync.timer
  systemctl --user restart jsb-mcp.service

  sleep 1
  echo "== status =="
  git -C "$repo" log --oneline -1
  systemctl --user --no-pager status jsb-mcp.service | head -8 || true
  systemctl --user --no-pager list-timers jsb-sync.timer --all | head -3 || true
  echo
  echo "logs -> journalctl --user -u jsb-mcp -f"
}

# Braced so bash parses everything before the in-script git pull can rewrite
# the file under the interpreter's read offset.
{ main "$@"; exit; }
