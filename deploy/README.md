# jobsearch-buddy deploy

Ansible role and day-2 playbook for running the jsb services on devbox.

## Role: jsb_services

Located at `deploy/ansible/roles/jsb_services/`. Deploys three systemd user units:

| Unit | Type | Purpose |
|---|---|---|
| `jsb-mcp.service` | daemon | MCP server bound to 127.0.0.1:8001 |
| `jsb-sync.service` | oneshot | Daily ATS scrape (timer-triggered, not enabled directly) |
| `jsb-sync.timer` | timer | Fires jsb-sync at 03:00 daily |

The role is consumed by `home-it-services` via an extended `roles_path` — it does
not live inside the `home-it-services` repo.

## Prerequisites (not managed by Ansible)

Before the services will start, provision manually on devbox:

```bash
# 1. Create the env file (secrets from 1Password)
#    Required variables: JOBBUDDY_OPENAI_API_KEY, plus any others jsb-mcp needs.
mkdir -p ~/.config
touch ~/.config/jsb-mcp.env    # populate from 1Password

# 2. Create the venv
cd ~/projects/jobsearch-buddy
uv sync

# 3. Enable user linger so units survive without an active login session
loginctl enable-linger $USER
```

## Day-2 deploy

Run from the **home-it-services Ansible control node** (Mac or devbox) so that
`inventory.yml` and MagicDNS resolution are available:

```bash
ansible-playbook \
  -i ~/projects/home-it-services/ansible/inventory.yml \
  ~/projects/jobsearch-buddy/deploy/deploy.yml
```

Selective tags:

```bash
# Code update only (git pull + uv sync), skip service convergence
ansible-playbook ... --tags pull

# Service convergence only (unit files + enable/start), skip code update
ansible-playbook ... --tags services
```

## Consuming the role from home-it-services

Add the jobsearch-buddy deploy path to `roles_path` in
`~/projects/home-it-services/ansible/ansible.cfg`:

```ini
[defaults]
roles_path = roles:~/projects/jobsearch-buddy/deploy/ansible/roles
```

Then reference the role by name in any home-it-services playbook:

```yaml
roles:
  - jsb_services
```

Override defaults in `inventory.yml` host_vars if the target user or UID differs
from the defaults (`coryking` / `1000`).

## jsb-sync EnvironmentFile fix

The unit deployed by this role adds `EnvironmentFile=%h/.config/jsb-mcp.env` to
`jsb-sync.service`. The original unit in `home-it-services` lacked this line, so
`jsb sync` never received `JOBBUDDY_OPENAI_API_KEY` and failed silently at the
OpenAI enrichment step. This role is the authoritative source for both units;
the `home-it-services` wigglebutt role's copies are superseded.
