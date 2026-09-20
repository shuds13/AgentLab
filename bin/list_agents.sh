#!/usr/bin/env bash
# List agents that are running right now: host, prompt, how recently they beat.
# Read-only. Use the run_id shown here with kill_agent.sh to stop one.
#
# Usage:
#   ./list_agents.sh          # agents running now
#   ./list_agents.sh --all    # every run, including finished ones and how they ended
#   ./list_agents.sh -n 5     # the last 5 runs, whatever state they are in
#
# Each run is one Claude session, kept after the run ends. The session id is printed
# with the run, and `claude -r <id>` reopens that conversation from anywhere -- what
# the agent was thinking, not only what it wrote.
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
STALE_AFTER=600     # s without a heartbeat before a run is presumed dead

# meta.json is read once per run and cached in scalar variables. macOS ships Bash 3.2,
# which has indexed arrays but not the associative arrays used by newer Bash versions.
META_LOADED_DIR=""
META_status=""
META_stop_reason=""
META_handle=""
META_host=""
META_pid=""
META_user_prompt_file=""
META_session_id=""

meta_load() {   # run_dir -> cache every field we print, as one call
    local d="$1"
    [ "$META_LOADED_DIR" = "$d" ] && return
    # Cleared first, and the directory recorded only once the read has happened, so a
    # run whose meta.json cannot be read prints blanks rather than the previous run's.
    META_status="" META_stop_reason="" META_handle="" META_host="" META_pid=""
    META_user_prompt_file="" META_session_id=""
    eval "$(python3 -c '
import json, shlex, sys
fields = ("status", "stop_reason", "handle", "host", "pid",
          "user_prompt_file", "session_id")
try:
    with open(sys.argv[1]) as f:
        meta = json.load(f)
except Exception:
    meta = {}
for k in fields:
    print("META_%s=%s" % (k, shlex.quote(str(meta.get(k, "") or "").replace("\\t", " "))))
' "$d/meta.json")"
    META_LOADED_DIR="$d"
}

meta_get() {
    meta_load "$1"
    eval "printf '%s' \"\${META_$2:-}\""
}


describe() {   # run_dir -> running / stopped (reason) / presumed dead
    local d="$1" status hb age now
    meta_load "$d"
    status="$META_status"
    if [ "$status" = "stopped" ]; then
        echo "stopped: $META_stop_reason"
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
    meta_load "$d"
    [ "$META_status" = "stopped" ] && return 1
    [ -f "$d/heartbeat" ] || return 1
    hb="$(cat "$d/heartbeat" 2>/dev/null || echo 0)"
    age=$(( $(date +%s) - hb ))
    [ "$age" -le "$STALE_AFTER" ]
}

ALL=0
LIMIT=0           # 0 = no limit
case "${1:-}" in
    --all) ALL=1 ;;
    -n) ALL=1; LIMIT="${2:-}"
        case "$LIMIT" in ''|*[!0-9]*|0) echo "usage: $0 -n <count>" >&2; exit 2 ;; esac ;;
    "") ;;
    *) echo "usage: $0 [--all | -n <count>]" >&2; exit 2 ;;
esac

shopt -s nullglob
dirs=( "${RUN_DIRS[@]}" )
if [ "${#dirs[@]}" -eq 0 ]; then
    echo "no runs in $RUNS_DIR"
    exit 0
fi

# Choose what to show before printing any of it, so the header can describe what is
# actually there rather than what might be.
show=()
# ls only ever gets real arguments here; with no args it would list '.' instead.
for d in $(ls -1dt "${dirs[@]}"); do
    d="${d%/}"
    if [ "$ALL" -eq 0 ] && ! is_running "$d"; then
        continue        # finished runs are history; --all shows them
    fi
    show+=( "$d" )
    [ "$LIMIT" -gt 0 ] && [ "${#show[@]}" -ge "$LIMIT" ] && break
done

if [ "${#show[@]}" -eq 0 ]; then
    if [ "$ALL" -eq 1 ]; then
        echo "no runs in $RUNS_DIR"
    else
        echo "no agents running  (${#dirs[@]} past run(s); --all to see them)"
    fi
    exit 0
fi

# Said once, and only when a finished run in this listing has a session to reopen.
for d in "${show[@]}"; do
    meta_load "$d"
    if [ -n "$META_session_id" ] && ! is_running "$d"; then
        echo "Reopen a finished run's conversation with: claude -r <session>"
        echo
        break
    fi
done

for d in "${show[@]}"; do
    meta_load "$d"
    handle="$META_handle"
    printf '%s%s\n    host=%s pid=%s prompt=%s\n    %s\n' \
        "$(basename "$d")" \
        "${handle:+  [$handle]}" \
        "$META_host" "$META_pid" \
        "$META_user_prompt_file" \
        "$(describe "$d")"
    sid="$META_session_id"
    # Only for runs that have ended. A live agent is still writing its conversation,
    # and reading it back is not what you want from a listing of what is running.
    if [ -n "$sid" ] && ! is_running "$d"; then
        # A transcript lives in the home directory of whoever ran it, on the machine
        # that ran it, and is pruned on that machine's own schedule. Another user's
        # run, another host, and a pruned one are all the same answer to the only
        # question worth asking: can this be opened from here.
        if compgen -G "$HOME/.claude/projects/*/$sid.jsonl" >/dev/null; then
            printf '    session: %s\n' "$sid"
        else
            printf '    session: %s (unavailable)\n' "$sid"
        fi
    fi
    if [ -f "$d/stop" ]; then
        echo "    stop requested (draining)"
    fi
done
