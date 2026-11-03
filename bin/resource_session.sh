#!/usr/bin/env bash
# Start, stop or show a group's resource session: one node-hour budget for all the runs
# in a workspace group, which share their blocks.
#
# Usage:
#   ./resource_session.sh <group> start swing=4 [polaris=10 ...]
#   ./resource_session.sh <group> stop
#   ./resource_session.sh <group>          the session and its node-hours so far
#
# <group> is the group's directory, workspace/<group>, or its name. framework/resource_session.py
# explains what is counted.
set -euo pipefail
exec python3 "$(cd "$(dirname "$0")/.." && pwd)/framework/resource_session.py" "$@"
