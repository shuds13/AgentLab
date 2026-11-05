#!/usr/bin/env bash
# The same run with a resource agent beside it: it watches Polaris's queues through IRI
# and sends the jobs to whichever of the debug and debug-scaling queues will start them
# sooner. Needs IRI access (docs/iri.md).
set -euo pipefail
cd "$(dirname "$0")"
export RESOURCE_AGENT=true
export RESOURCE_INTERVAL="${RESOURCE_INTERVAL:-180}"
exec ./run.sh "$@"
