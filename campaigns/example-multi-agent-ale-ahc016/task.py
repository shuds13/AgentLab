"""The coordinator's job: one worker agent, run to completion on its own branch.

Each local job starts a worker through the framework's spawn module: a full agent run
in this campaign, in its own worktree of the search repo, following the method for its
kind. The worker's own job tools come from eval_task.py.

The workers share one evaluation budget (WORKER_JOB_BUDGET in run.sh).
"""

import os

from spawn import run_worker

REPO = os.path.join(os.environ["WORKSPACE_DIR"], "repo")

# Explorers build an approach from nothing, so they get the stronger model; refiners make
# one change at a time to a working solution and continue an explorer's conversation.
# Set in run.sh (EXPLORER_MODEL, REFINER_MODEL). Workers read, write and edit files and
# evaluate through their job tool; they have no shell, so every evaluation is an
# official one.
WORKER_TOOLS = "Read Write Edit Glob Grep"

ROLES = {
    "explorer": {"method": "explorer.md", "task_module": "eval_task",
                 "max_submits": 8, "max_runtime": 2700, "fork": False,
                 "model": os.environ.get("EXPLORER_MODEL"),
                 "records": ["findings.md"], "tools": WORKER_TOOLS},
    "refiner": {"method": "refiner.md", "task_module": "eval_task",
                "max_submits": 10, "max_runtime": 2700, "fork": True,
                "model": os.environ.get("REFINER_MODEL"),
                "records": ["findings.md"], "tools": WORKER_TOOLS},
}

LOCAL_DESC = f"""
Run one worker agent to completion on its own branch of the search repo, and return
what it produced.

Parameters:
  kind            explorer | refiner.
  branch          A new, non-descriptive branch name for this worker, e.g. researcher-17.
                  It is created from `parent` and checked out in its own worktree. A
                  branch you have already prepared (a multi-parent merge) is used as it is.
  parent          The branch or commit the worker starts from: `master` for a fresh
                  approach, another worker's branch to build on it. Empty for a branch
                  you prepared.
  parent_session  Refiner only: the session_id of the worker whose conversation it
                  continues. Empty for an explorer.
  context         What you want the worker to know, added to its prompt. Empty for none.

Returns the worker's `session_id` (pass it as parent_session to continue it later),
its `summary`, the `score` and `commit` of its newest scored commit, `cost_usd`,
`run_id`, `jobs_given` and `jobs_used`, and `shared_jobs_left` for the team. Workers take
a few minutes.

Each worker counts against this run's job budget. An explorer gets 8 evaluations and a
refiner 10, taken from the {os.environ.get("WORKER_JOB_BUDGET", "unlimited")} the workers
share; what a worker does not use returns to the pool.
"""

LOCAL_SCHEMA = {"kind": str, "branch": str, "parent": str, "parent_session": str,
                "context": str}


def local_fn(args):
    return run_worker(args, repo=REPO, roles=ROLES)
