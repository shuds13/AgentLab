#!/usr/bin/env bash
# Watch a lab that is on another machine.
#
# Run it on your local computer. It logs in once, starts the watcher on the remote host,
# and opens your browser. Ctrl-C stops both.
#
# Usage: ./watch_remote.sh [host] [remote-repo-path] [--no-open]
#
#   host               what you would type after `ssh`, e.g. gce, or user@host
#   remote-repo-path   where AgentLab is on that machine
#
# Set HOST, REPO and ACTIVATE below and it takes no arguments at all; an argument
# overrides what is set there. ACTIVATE runs on the far side before the watcher.
#
# E.g.,
#   HOST="compute-386-07.cels.anl.gov"
#   REPO="<path>/AgentLab"
#   ACTIVATE="source \$HOME/miniconda3/etc/profile.d/conda.sh && conda activate agentlab"
set -euo pipefail

AUTO_OPEN=true

HOST=""
REPO="\$HOME/AgentLab"
ACTIVATE=""

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
REMOTE_PID=""
REMOTE_LOG=""

cleanup() {
    if [ -n "$REMOTE_PID" ]; then
        ssh -S "$CTL" -O check "$HOST" 2>/dev/null &&
            ssh -S "$CTL" "$HOST" "kill $REMOTE_PID 2>/dev/null; rm -f $REMOTE_LOG" \
                >/dev/null 2>&1 || true
    fi
    ssh -S "$CTL" -O exit "$HOST" >/dev/null 2>&1 || true
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

# One login. Everything after this reuses the socket, so no further prompts.
echo "[watch] connecting to $HOST"
ssh -M -S "$CTL" -o ControlPersist=60 -f -N "$HOST"

# Start the watcher and learn which port it actually took. It is left running over
# there for the moment because the forward cannot be added until the port is known.
REMOTE_LOG="/tmp/watch_remote.$USER.$$.log"
# </dev/null matters: a backgrounded remote process still holding the session's stdin
# keeps the channel open, and this ssh would never return.
REMOTE_PID="$(ssh -n -S "$CTL" "$HOST" \
    "{ cd '$REPO' && ${ACTIVATE:+$ACTIVATE && } \
       exec setsid python3 framework/watch.py --no-open ; } \
     > $REMOTE_LOG 2>&1 </dev/null & echo \$!")"
[ -n "$REMOTE_PID" ] || { echo "the watcher did not start" >&2; exit 1; }

REMOTE_PORT=""
for _ in $(seq 0 30); do
    REMOTE_PORT="$(ssh -S "$CTL" "$HOST" \
        "grep -o 'http://127.0.0.1:[0-9]*' $REMOTE_LOG 2>/dev/null | head -1 | grep -o '[0-9]*$'" \
        2>/dev/null || true)"
    [ -n "$REMOTE_PORT" ] && break
    sleep 0.5
done
if [ -z "$REMOTE_PORT" ]; then
    echo "[watch] the watcher started but said no port. Its output:" >&2
    ssh -S "$CTL" "$HOST" "cat $REMOTE_LOG" >&2 || true
    exit 1
fi

# Add the forward to the connection that is already authenticated.
LOCAL_PORT="$(free_port "$REMOTE_PORT")"
ssh -S "$CTL" -O forward -L "$LOCAL_PORT:127.0.0.1:$REMOTE_PORT" "$HOST"

URL="http://127.0.0.1:$LOCAL_PORT/"
echo "[watch] watching $HOST:$REMOTE_PORT at $URL   (Ctrl-C closes it)"
[ "$AUTO_OPEN" = true ] && { (xdg-open "$URL" || open "$URL") >/dev/null 2>&1 || true; }

# Hold the foreground so Ctrl-C reaches the trap, and stop by itself if the watcher
# over there exits or the connection drops.
while ssh -S "$CTL" "$HOST" "kill -0 $REMOTE_PID" >/dev/null 2>&1; do
    sleep 2
done
echo "[watch] the watcher on $HOST has stopped"
