#!/bin/bash
# SessionStart hook: cheap, idempotent, always exits 0. The JDK, browsers and images belong to the
# environment preflight (docs/runbook.md 2(c)), not to this hook. It never reads data.env.
ENV_FILE="$HOME/.config/modernization/env"
# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
if ! command -v uv >/dev/null 2>&1; then
  echo "run the environment preflight"
  exit 0
fi
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" 2>/dev/null || exit 0
uv sync --locked --all-groups -q >/dev/null 2>&1 || echo "uv sync --locked failed: run it by hand"
docker start mp-pg16 mp-mssql22 >/dev/null 2>&1 || true
if [ -n "${CLAUDE_ENV_FILE:-}" ] && [ -f "$ENV_FILE" ]; then
  grep '^export [A-Z_][A-Z0-9_]*=' "$ENV_FILE" >>"$CLAUDE_ENV_FILE" 2>/dev/null || true
fi
py=$(uv run --no-sync python -c 'import platform; print(platform.python_version())' 2>/dev/null || echo none)
containers=$(docker ps --format '{{.Names}}' 2>/dev/null | tr '\n' ' ')
echo "$(uv --version 2>/dev/null); python ${py}; containers: ${containers:-none}"
exit 0
