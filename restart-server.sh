#!/usr/bin/env bash
# Restart this checkout's configured local server as the current user.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if (( EUID == 0 )); then
    echo "Run this script as your normal user." >&2
    exit 1
fi

if [[ ! -f .local-server/start_server.py ]]; then
    echo "This checkout has no configured local server (.local-server/start_server.py)." >&2
    exit 1
fi

for interpreter in "${SLOWBOOKS_PYTHON:-}" .venv/bin/python /tmp/slowbooks-qbo-test-venv/bin/python; do
    if [[ -n "$interpreter" && -x "$interpreter" ]]; then
        exec "$interpreter" .local-server/start_server.py --restart
    fi
done

echo "Server Python not found. Set SLOWBOOKS_PYTHON to your virtual environment's Python." >&2
exit 1
