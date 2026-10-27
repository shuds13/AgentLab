#!/usr/bin/env bash
# Follow a running campaign in a browser: the agent's log as it is written, and the
# files it is writing. Localhost, and the run is unaffected by starting or stopping it.
# The link it prints carries the key the page needs to make changes.
#
# Usage: ./watch.sh [campaign] [--port N] [--no-open]
# With no campaign it opens on the one most recently active; the page switches between all.
set -euo pipefail
cd "$(dirname "$0")"
# The page reports whether this lab posts to Slack, so it reads the lab's settings
# rather than looking for a webhook of its own.
. ../framework/settings.sh
exec python3 ../framework/watch.py "$@"
