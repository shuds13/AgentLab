#!/usr/bin/env bash
# Show or change where a workspace's tasks go: redirects from one resource bucket to
# another, for all its runs, from their next submit.
#
# Usage:
#   ./route.sh <workspace>                     # the redirects in force
#   ./route.sh <workspace> debug=debug_scaling # send debug's tasks to debug_scaling
#   ./route.sh <workspace> debug=              # stop redirecting debug
#
# <workspace> is a workspace under workspace/, by name or path. Only buckets that give a
# job the same number of nodes may be redirected to each other; a redirect that does not
# fit is ignored at submit. A resource agent (ROLE=resources) makes the same change with
# its set_route tool. framework/routing.py has the details.
set -euo pipefail
exec python3 "$(cd "$(dirname "$0")/.." && pwd)/framework/routing.py" "$@"
