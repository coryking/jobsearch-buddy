#!/usr/bin/env bash
# Build a deployment package for Azure Functions (Flex Consumption).
#
# Usage:
#   ./scripts/build-function-app.sh --target /tmp/deploy
#
# Produces a directory at TARGET with:
#   - .python_packages/lib/site-packages/ (all dependencies)
#   - src/jobbuddy/ (application code)
#   - host.json (custom handler config)
#   - version.txt (git sha + timestamp)

set -euo pipefail

TARGET=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --target) TARGET="$2"; shift 2 ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$TARGET" ]]; then
    echo "Usage: $0 --target <directory>" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Cleaning target: $TARGET"
rm -rf "$TARGET"
mkdir -p "$TARGET"

echo "==> Installing dependencies to $TARGET/.python_packages/lib/site-packages"
uv pip install \
    --quiet \
    --python "$(which python3)" \
    --target "$TARGET/.python_packages/lib/site-packages" \
    "$REPO_ROOT"

echo "==> Copying host.json"
cp "$REPO_ROOT/host.json" "$TARGET/host.json"

echo "==> Generating version.txt"
GIT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
echo "${GIT_SHA} $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$TARGET/version.txt"

echo "==> Verifying imports"
PYTHONPATH="$TARGET/.python_packages/lib/site-packages" \
    python3 -c "import jobbuddy; import fastmcp; import azure.identity; import redis" \
    || { echo "FAIL: import verification failed" >&2; exit 1; }

echo "==> Build complete: $TARGET"
echo "    $(find "$TARGET" -type f | wc -l | tr -d ' ') files"
