#!/usr/bin/env bash
# Run LiteLLM with a local Postgres database managed by pixi.
#
# Usage:
#   pixi run litellm-proxy-start  # start PostgreSQL and the proxy
#   pixi run litellm-proxy-stop   # stop the proxy, then PostgreSQL
#
# Press Ctrl-C in the start command to stop the proxy before PostgreSQL.
# Override LITELLM_PORT, LITELLM_DB_PORT, LITELLM_MASTER_KEY,
# LITELLM_SALT_KEY, or LITELLM_PGDATA when needed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PGDATA="${LITELLM_PGDATA:-$ROOT_DIR/scratch/litellm-postgres}"
PGLOG="${LITELLM_PGLOG:-$ROOT_DIR/scratch/litellm-postgres.log}"
PIDFILE="${LITELLM_PIDFILE:-$ROOT_DIR/scratch/litellm-proxy.pid}"
DB_PORT="${LITELLM_DB_PORT:-5433}"
PROXY_PORT="${LITELLM_PORT:-4000}"
DB_USER="${LITELLM_DB_USER:-litellm}"
DB_NAME="${LITELLM_DB_NAME:-litellm}"
DB_URL="${DATABASE_URL:-postgresql://${DB_USER}@127.0.0.1:${DB_PORT}/${DB_NAME}}"

stop_proxy() {
    local pid=""
    if [ -f "$PIDFILE" ]; then
        pid="$(cat "$PIDFILE")"
    fi
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid"
        for _ in $(seq 1 30); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        if kill -0 "$pid" 2>/dev/null; then
            echo "LiteLLM did not stop within 30 seconds" >&2
            return 1
        fi
    fi
    rm -f "$PIDFILE"
}

stop_db() {
    if [ -d "$PGDATA" ] && pg_ctl -D "$PGDATA" status >/dev/null 2>&1; then
        pg_ctl -D "$PGDATA" -m fast stop
    fi
}

if [ "${1:-start}" = "stop" ]; then
    stop_proxy
    stop_db
    exit 0
fi

if [ "${1:-start}" != "start" ]; then
    echo "usage: $0 [start|stop]" >&2
    exit 2
fi

mkdir -p "$(dirname "$PGDATA")"
if [ ! -f "$PGDATA/PG_VERSION" ]; then
    initdb -D "$PGDATA" -U "$DB_USER" --auth=trust --no-instructions
fi

if pg_ctl -D "$PGDATA" status >/dev/null 2>&1; then
    if ! pg_isready -h 127.0.0.1 -p "$DB_PORT" >/dev/null 2>&1; then
        echo "Postgres data directory is running, but not on port $DB_PORT: $PGDATA" >&2
        exit 1
    fi
else
    pg_ctl -D "$PGDATA" -o "-p $DB_PORT -h 127.0.0.1" -l "$PGLOG" start
fi

until pg_isready -h 127.0.0.1 -p "$DB_PORT" >/dev/null 2>&1; do
    sleep 1
done

if ! psql -h 127.0.0.1 -p "$DB_PORT" -U "$DB_USER" -d postgres -Atqc \
    "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q '^1$'; then
    createdb -h 127.0.0.1 -p "$DB_PORT" -U "$DB_USER" "$DB_NAME"
fi

SCHEMA="$(python -c 'import os, litellm; print(os.path.join(os.path.dirname(litellm.__file__), "proxy", "schema.prisma"))')"
if [ ! -f "$(dirname "$SCHEMA")/client.py" ]; then
    prisma generate --schema="$SCHEMA"
fi

export DATABASE_URL="$DB_URL"
export LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-1234}"
export LITELLM_SALT_KEY="${LITELLM_SALT_KEY:-sk-local-development-only}"
export STORE_MODEL_IN_DB="${STORE_MODEL_IN_DB:-True}"

litellm --config "$ROOT_DIR/config.yaml" --port "$PROXY_PORT" &
proxy_pid=$!
printf '%s\n' "$proxy_pid" > "$PIDFILE"
cleanup() {
    trap - INT TERM EXIT
    stop_proxy || true
    stop_db || true
}
trap cleanup INT TERM EXIT

set +e
wait "$proxy_pid"
proxy_status=$?
set -e
cleanup
exit "$proxy_status"
