#!/usr/bin/env bash
# Open a shell inside a run's sandbox: the same box its agent works in (SANDBOX=bwrap),
# to see what the agent can see, write and signal.
#
# Usage:
#   ./sandbox_shell.sh <workspace>                  # the newest run's box
#   ./sandbox_shell.sh <workspace> --run <run_id>   # a given run's, e.g. a worker's
#
# <workspace> is the campaign's workspace under workspace/, by name or path.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
[ $# -ge 1 ] || { sed -n '2,9p' "$0"; exit 1; }
ws="$1"
[ -d "$ws" ] || ws="$root/workspace/$1"
[ -d "$ws" ] || { echo "sandbox_shell: no workspace $1"; exit 1; }
if [ "${2:-}" = "--run" ] && [ -n "${3:-}" ]; then
  run="$3"
else
  run="$(cd "$root/framework" && python3 -c 'import sys, budgets; print(budgets._newest_top_run(sys.argv[1]) or "")' "$ws")"
fi
box="$ws/runs/$run/sandbox.sh"
[ -n "$run" ] && [ -x "$box" ] || { echo "sandbox_shell: no sandbox for run '${run}' in $ws (was it started with SANDBOX=bwrap?)"; exit 1; }
echo "sandbox_shell: run $run -- exit to leave"
# A plain shell: the box has a home of its own, without your startup files.
exec "$box" env -u PROMPT_COMMAND PS1='(sandbox) \w\$ ' bash --norc -i
