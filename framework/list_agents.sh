#!/usr/bin/env bash
# List agents that are running right now: host, prompt, how recently they beat.
# Read-only. Use the run_id shown here with kill_agent.sh to stop one.
#
# Usage:
#   ./list_agents.sh          # agents running now
#   ./list_agents.sh --all    # every run, including finished ones and how they ended
#
# WORKSPACE_DIR must match the agent's (default is every campaign in the lab).
set -euo pipefail

shopt -s nullglob
LAB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# One campaign if WORKSPACE_DIR is set; otherwise every campaign in the lab.
if [ -n "${WORKSPACE_DIR:-}" ]; then
    RUN_DIRS=( "$WORKSPACE_DIR"/runs/*/ )
else
    RUN_DIRS=( "$LAB_DIR"/workspace/*/runs/*/ )
fi
RUNS_DIR="${WORKSPACE_DIR:-$LAB_DIR/workspace/*}/runs"
STALE_AFTER=300     # s without a heartbeat before a run is presumed dead

meta_get() {
    python3 -c '
import json, sys
try:
    with open(sys.argv[1]) as f:
        print(json.load(f).get(sys.argv[2], "") or "")
except Exception:
    print("")
' "$1/meta.json" "$2"
}

describe() {   # run_dir -> running / stopped (reason) / presumed dead
    local d="$1" status hb age now
    status="$(meta_get "$d" status)"
    if [ "$status" = "stopped" ]; then
        echo "stopped: $(meta_get "$d" stop_reason)"
        return
    fi
    if [ ! -f "$d/heartbeat" ]; then
        echo "no heartbeat (never started, or died before its first beat)"
        return
    fi
    hb="$(cat "$d/heartbeat" 2>/dev/null || echo 0)"
    now="$(date +%s)"
    age=$(( now - hb ))
    if [ "$age" -le "$STALE_AFTER" ]; then
        echo "running (heartbeat ${age}s ago)"
    else
        echo "no heartbeat for $(( age / 60 ))m (presumed dead)"
    fi
}

is_running() {   # a run is running only if it is beating now
    local d="$1" hb age
    [ "$(meta_get "$d" status)" = "stopped" ] && return 1
    [ -f "$d/heartbeat" ] || return 1
    hb="$(cat "$d/heartbeat" 2>/dev/null || echo 0)"
    age=$(( $(date +%s) - hb ))
    [ "$age" -le "$STALE_AFTER" ]
}

ALL=0
[ "${1:-}" = "--all" ] && ALL=1

shopt -s nullglob
dirs=( "${RUN_DIRS[@]}" )
if [ "${#dirs[@]}" -eq 0 ]; then
    echo "no runs in $RUNS_DIR"
    exit 0
fi

shown=0
# ls only ever gets real arguments here; with no args it would list '.' instead.
for d in $(ls -1dt "${dirs[@]}"); do
    d="${d%/}"
    if [ "$ALL" -eq 0 ] && ! is_running "$d"; then
        continue        # finished runs are history; --all shows them
    fi
    shown=1
    printf '%s\n    host=%s pid=%s prompt=%s\n    %s\n' \
        "$(basename "$d")" \
        "$(meta_get "$d" host)" "$(meta_get "$d" pid)" \
        "$(meta_get "$d" user_prompt_file)" \
        "$(describe "$d")"
    if [ -f "$d/stop" ]; then
        echo "    stop requested (draining)"
    fi
done

if [ "$shown" -eq 0 ]; then
    if [ "$ALL" -eq 1 ]; then
        echo "no runs in $RUNS_DIR"
    else
        echo "no agents running  (${#dirs[@]} past run(s); --all to see them)"
    fi
fi
