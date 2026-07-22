# jobsearch-buddy deploy

Self-contained deploy for the jsb services on **devbox** — no config-management
layer, no Ansible, no inventory. `deploy/deploy.sh` run on devbox is the whole
deploy. (Same pattern as wirecap: free-wheeling dev boxes deploy from their own
repo — script + systemd --user units.)

## Units

Plain unit files live in this directory and are the authoritative source
(`~/.config/systemd/user/` copies are installed artifacts):

| Unit | Type | Purpose |
|---|---|---|
| `jsb-mcp.service` | daemon | MCP server bound to 127.0.0.1:8001 |
| `jsb-sync.service` | oneshot | Daily ATS scrape (timer-triggered, never enabled directly) |
| `jsb-sync.timer` | timer | Fires jsb-sync at 03:00 daily |

## Day-2 deploy

```bash
# on devbox
deploy/deploy.sh

# from any other box (Tailscale SSH — keyless, works headless)
ssh devbox '~/projects/jobsearch-buddy/deploy/deploy.sh'
```

The script: `git pull --ff-only` → `uv sync` → install unit files →
`daemon-reload` → enable `jsb-mcp.service` + `jsb-sync.timer` → restart
`jsb-mcp.service`.

## First-time provisioning (manual, once per box)

```bash
# 1. Env file (secrets from 1Password)
#    Required variables: JOBBUDDY_OPENAI_API_KEY, plus any others jsb-mcp needs.
touch ~/.config/jsb-mcp.env    # populate from 1Password

# 2. User linger, so the units survive without an active login session
loginctl enable-linger $USER
```

The venv itself needs no manual step — `deploy.sh` runs `uv sync`.
