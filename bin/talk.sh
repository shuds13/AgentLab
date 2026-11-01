#!/usr/bin/env bash
# Talk with the agent of a run that has ended: its session reopened as it ran, with its
# file tools and none of its job tools. See framework/talk.py.
#
# Usage: ./talk.sh <run dir | workspace dir> [-m "message"] [--new]
# A workspace dir means its newest run.
set -euo pipefail
cd "$(dirname "$0")"
. ../framework/settings.sh
TARGET="${1:?usage: $0 <run dir | workspace dir> [-m message] [--new]}"; shift
TARGET="$(cd "$OLDPWD" && cd "$TARGET" && pwd)"
if [ ! -f "$TARGET/meta.json" ] && [ -d "$TARGET/runs" ]; then
    TARGET="$(ls -td "$TARGET"/runs/*/ | head -1)"
fi
exec python3 ../framework/talk.py "$TARGET" "$@"
