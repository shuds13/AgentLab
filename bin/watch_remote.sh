#!/usr/bin/env bash
# Watch a lab that is on another machine.
#
# Run it on your local computer. It logs in once, starts the watcher on the remote host,
# and opens your browser. Ctrl-C stops both.
#
# Usage: ./watch_remote.sh [host] [remote-repo-path] [--no-open]
#
#   host               what you would type after `ssh`, e.g. gce, or user@host
#   remote-repo-path   the directory WATCHER is given relative to
#
# Set HOST, REPO, WATCHER and ACTIVATE below and it takes no arguments at all; an
# argument overrides what is set there. ACTIVATE runs on the far side before the watcher.
#
# WATCHER is where watch.py is on that machine. A full lab keeps it in the framework, so
# the default suits one. A workspace copied somewhere on its own is watched by putting
# watch.py beside it and naming it here -- nothing else of the lab need be there.
#
# E.g., a lab:
#   HOST="compute-386-07.cels.anl.gov"
#   REPO="<path>/AgentLab"
#   ACTIVATE="source \$HOME/miniconda3/etc/profile.d/conda.sh && conda activate agentlab"
#
# E.g., a workspace on its own, with watch.py next to it:
#   HOST="polaris.alcf.anl.gov"
#   REPO="/lus/eagle/projects/<project>/<mirror>"
#   WATCHER="watch.py"
#   ACTIVATE="module use /soft/modulefiles; module load conda; conda activate"
set -euo pipefail

AUTO_OPEN=true

HOST=""
REPO="\$HOME/AgentLab"
WATCHER="framework/watch.py"
ACTIVATE=""
# Seconds the remote watcher will sit with nobody looking before stopping itself. An
# open page polls every second or so, so this only runs down once the tunnel is gone --
# which is the one case where nothing here can reach over and stop it.
IDLE=600

POSITIONAL=()

while [ $# -gt 0 ]; do
    case "$1" in
        --no-open) AUTO_OPEN=false ;;
        -*) echo "unknown option: $1" >&2; exit 2 ;;
        *) POSITIONAL+=("$1") ;;
    esac
    shift
done
if [ ${#POSITIONAL[@]} -ge 1 ]; then HOST="${POSITIONAL[0]}"; fi
if [ ${#POSITIONAL[@]} -ge 2 ]; then REPO="${POSITIONAL[1]}"; fi
if [ -z "$HOST" ]; then echo "no host: set HOST in this script, or pass one" >&2; exit 2; fi

CTL="${TMPDIR:-/tmp}/watch_remote.$$.sock"
SSH=(ssh -S "$CTL")
OWN_CONNECTION=true
FORWARD=""
REMOTE_PID=""
REMOTE_LOG=""

cleanup() {
    if [ -n "$REMOTE_PID" ]; then
        "${SSH[@]}" -O check "$HOST" 2>/dev/null &&
            "${SSH[@]}" "$HOST" "kill $REMOTE_PID 2>/dev/null; rm -f $REMOTE_LOG" \
                >/dev/null 2>&1 || true
    fi
    if [ "$OWN_CONNECTION" = true ]; then
        "${SSH[@]}" -O exit "$HOST" >/dev/null 2>&1 || true
    elif [ -n "$FORWARD" ]; then
        "${SSH[@]}" -O cancel -L "$FORWARD" "$HOST" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT INT TERM

# A free port on THIS side. The far side chooses its own, and the two need not match.
free_port() {
    local p="$1"
    for _ in $(seq 0 19); do
        if ! (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then echo "$p"; return; fi
        exec 3<&- 2>/dev/null || true
        p=$((p + 1))
    done
    echo "no free local port between $1 and $(($1 + 19))" >&2
    exit 1
}

# One login. Everything after this reuses the connection, so no further prompts. A
# connection already open to the host (ControlMaster in ~/.ssh/config) is used as it is
# and left open, so a host that asks for a one-time password asks once, not on every
# start.
if ssh -O check "$HOST" >/dev/null 2>&1; then
    echo "[watch] using the open connection to $HOST"
    SSH=(ssh)
    OWN_CONNECTION=false
else
    echo "[watch] connecting to $HOST"
    # ServerAlive keeps the master from being dropped for silence: lose it and the remote
    # watcher is unreachable, so it can neither be stopped nor asked whether it is alive
    # -- and the next command prompts for a login again.
    ssh -M -S "$CTL" -o ControlPersist=60 -o ServerAliveInterval=30 -o ServerAliveCountMax=6 \
        -f -N "$HOST"
fi

# Start the watcher and learn which port it actually took. It is left running over
# there for the moment because the forward cannot be added until the port is known.
REMOTE_LOG="/tmp/watch_remote.$USER.$$.log"
# </dev/null matters: a backgrounded remote process still holding the session's stdin
# keeps the channel open, and this ssh would never return.
REMOTE_PID="$("${SSH[@]}" -n "$HOST" \
    "{ cd '$REPO' && ${ACTIVATE:+$ACTIVATE && } \
       exec setsid python3 '$WATCHER' --no-open --exit-when-idle=$IDLE ; } \
     > $REMOTE_LOG 2>&1 </dev/null & echo \$!")"
[ -n "$REMOTE_PID" ] || { echo "the watcher did not start" >&2; exit 1; }

REMOTE_URL=""
for _ in $(seq 0 30); do
    REMOTE_URL="$("${SSH[@]}" "$HOST" \
        "grep -o 'http://127.0.0.1:[0-9]*/[?k=A-Za-z0-9_-]*' $REMOTE_LOG 2>/dev/null | head -1" \
        2>/dev/null || true)"
    [ -n "$REMOTE_URL" ] && break
    sleep 0.5
done
# The port, and the key the page needs to make changes, from the link it printed.
REMOTE_PORT="$(echo "$REMOTE_URL" | sed -n 's|^http://127.0.0.1:\([0-9]*\).*|\1|p')"
KEY_QUERY="$(echo "$REMOTE_URL" | sed -n 's|^http://127.0.0.1:[0-9]*/||p')"
if [ -z "$REMOTE_PORT" ]; then
    echo "[watch] the watcher started but said no port. Its output:" >&2
    "${SSH[@]}" "$HOST" "cat $REMOTE_LOG" >&2 || true
    exit 1
fi

# Add the forward to the connection that is already authenticated.
LOCAL_PORT="$(free_port "$REMOTE_PORT")"
FORWARD="$LOCAL_PORT:127.0.0.1:$REMOTE_PORT"
"${SSH[@]}" -O forward -L "$FORWARD" "$HOST"

URL="http://127.0.0.1:$LOCAL_PORT/$KEY_QUERY"
echo "[watch] watching $HOST:$REMOTE_PORT at $URL   (Ctrl-C closes it)"
[ "$AUTO_OPEN" = true ] && { (xdg-open "$URL" || open "$URL") >/dev/null 2>&1 || true; }

# Hold the foreground so Ctrl-C reaches the trap, and stop by itself if the watcher
# over there exits or the connection drops.
while "${SSH[@]}" "$HOST" "kill -0 $REMOTE_PID" >/dev/null 2>&1; do
    sleep 2
done
echo "[watch] the watcher on $HOST has stopped"
