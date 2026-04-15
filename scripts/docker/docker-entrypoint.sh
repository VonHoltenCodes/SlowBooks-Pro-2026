#!/bin/sh
set -eu

exec /bin/sh /app/scripts/docker-entrypoint.sh "$@"
