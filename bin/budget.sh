#!/usr/bin/env bash
# Show or change a run's budgets: every limit it stops at, as totals for the whole run.
#
# Usage:
#   ./budget.sh <workspace>                                  # the newest run's budgets
#   ./budget.sh <workspace> submits=+10 node_hours.polaris=+5 workers.explorer=+4
#   ./budget.sh <workspace> --run <run_id> runtime_s=+2h
#
# <workspace> is the campaign's workspace under workspace/, by name or path. A change is
# NAME[.KEY]=+N to add, =-N to take away, or =N to set. A running agent is told of it at
# its next turn, and one winding down because the budget was spent goes on; a run that
# has ended takes it up when continued (CONTINUE_RUN). The names are in
# framework/budgets.py.
set -euo pipefail
exec python3 "$(cd "$(dirname "$0")/.." && pwd)/framework/budgets.py" "$@"
