#!/usr/bin/env bash
# Stop a running agent (or see which are running).
#
# Usage:
#   ./kill_agent.sh                        # agents running now
#   ./kill_agent.sh --drain <run_id>       # ask the agent to wind down cleanly
#   ./kill_agent.sh --now   <run_id>       # SIGTERM now (abrupt)
#   ./kill_agent.sh --now   <run_id> --force   # SIGTERM even if the run is on another host
#
# --drain is the normal way to stop an agent. It creates a `stop` file in the run's
# directory; the agent notices within a couple of seconds, stops submitting new work,
# finishes and logs the jobs already in flight, writes up the cycle (journal +
# LOGBOOK), then exits and posts its finish message to Slack. It sends no signal, so
# it works from any node and cannot hit the wrong process.
#
# --now sends SIGTERM. Shutdown is still graceful (finish ping + executor shutdown),
# but in-flight jobs are abandoned: they keep running remotely and their results
# become uncollectable until a later run recovers them. Use it only when a drain is
# not possible. A signal only reaches a process on the SAME host, so --now refuses to
# run when the run's recorded host is not this one (--force overrides, but the PID may
# belong to an unrelated process on this machine).
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

# Read one field out of a run's meta.json.
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

describe() {   # run_dir -> "running" / "stopped (reason)" / "no heartbeat for Nm (presumed dead)"
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

list() {         # agents running now -- the only ones that can be stopped
    shopt -s nullglob
    local dirs=( "${RUN_DIRS[@]}" )
    if [ "${#dirs[@]}" -eq 0 ]; then
        echo "no runs in $RUNS_DIR"
        return
    fi
    local d shown=0
    # ls only ever gets real arguments here; with no args it would list '.' instead.
    for d in $(ls -1dt "${dirs[@]}"); do
        d="${d%/}"
        if ! is_running "$d"; then
            continue      # finished runs cannot be stopped; see ./list_agents.sh --all
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
        echo "no agents running  (./list_agents.sh --all shows finished runs)"
    fi
}

if [ "$#" -eq 0 ]; then
    list
    exit 0
fi

MODE="$1"
RUN_ID="${2:-}"
FORCE="${3:-}"
# Find the run by id across whichever campaigns are in scope.
RUN_PATH=""
for d in "${RUN_DIRS[@]}"; do
    [ -d "$d" ] || continue
    if [ "$(basename "${d%/}")" = "$RUN_ID" ]; then RUN_PATH="${d%/}"; break; fi
done

if [ -z "$RUN_ID" ] || { [ "$MODE" != "--drain" ] && [ "$MODE" != "--now" ]; }; then
    echo "usage: $0 [--drain|--now] <run_id> [--force]" >&2
    echo "       $0            # agents running now" >&2
    exit 2
fi

if [ ! -d "$RUN_PATH" ]; then
    echo "no such run: $RUN_PATH" >&2
    echo "runs:" >&2
    list >&2
    exit 1
fi

if [ "$MODE" = "--drain" ]; then
    if [ -f "$RUN_PATH/stop" ]; then
        echo "stop already requested for $RUN_ID; it is draining."
        exit 0
    fi
    : > "$RUN_PATH/stop"
    echo "stop requested for $RUN_ID."
    echo "It will finish its in-flight jobs, write up the cycle, then exit and post to Slack."
    echo "Watch: $(meta_get "$RUN_PATH" log)"
    exit 0
fi

# --now: SIGTERM, only safe on the host that owns the run.
HOST="$(meta_get "$RUN_PATH" host)"
PID="$(meta_get "$RUN_PATH" pid)"
HERE="$(hostname)"

if [ "$HOST" != "$HERE" ] && [ "$FORCE" != "--force" ]; then
    echo "refusing: $RUN_ID runs on '$HOST' but this is '$HERE'." >&2
    echo "A signal cannot cross hosts, and pid $PID here is probably an unrelated process." >&2
    echo "Use --drain (works from any node), or run --now on $HOST." >&2
    exit 1
fi

if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    echo "pid '$PID' for $RUN_ID is not running here. Current state: $(describe "$RUN_PATH")"
    exit 0
fi

echo "sending SIGTERM to $RUN_ID (pid $PID) -- in-flight jobs will be abandoned..."
kill -TERM "$PID"

for _ in $(seq 1 30); do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "stopped."
        exit 0
    fi
    sleep 1
done

echo "still running after 30s (a local SA batch can hold shutdown until it finishes)." >&2
echo "Re-run to retry, or force with: kill -9 $PID (no finish ping)." >&2
exit 1
