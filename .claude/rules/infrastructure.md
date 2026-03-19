---
description: Guidance for working on Azure infrastructure (Terraform, deployment, managed identity)
globs: infrastructure/**/*.tf, scripts/build-function-app.sh, .github/workflows/*.yml, src/jobbuddy/mcp_server.py
---

# Infrastructure Patterns

Blue Taka (`~/projects/bluetaka/`) is a reference for Azure patterns, not a
template. When adapting patterns from bluetaka:

- Use the latest provider versions (don't pin to bluetaka's older versions)
- Simplify where bluetaka over-engineered (it has multi-env, multi-region
  complexity this repo doesn't need)
- Question inherited patterns — bluetaka solved different problems
- Inline rather than modularize prematurely (one deploy target = no modules
  except where Terraform requires them, like the postgres-entra-principal hack)
