"""An agent's job: score main.cpp in its working directory on the 50 public test cases
and commit the directory with the result.

Each evaluation is committed as `{description} | score = {mean score}`, or
`{description} | failed: {reason}` when it does not compile, so the branch history is
the agent's record and scores can be read from it.
"""

import json
import os
import subprocess

# Outside every agent's working directory, so no agent can read the evaluator or the
# test cases.
EVAL_DIR = os.environ["EVAL_DIR"]
EVAL_TIMEOUT = 900

LOCAL_DESC = """
Compile main.cpp in your working directory, run it on the 50 public test cases and
score each with AtCoder's official tester, then commit your working directory with the
result.

Parameters:
  description  A concise description of the experiment. It becomes the commit message,
               followed by the score.

Returns `score` (the mean score over the 50 cases; a failed case counts as 0), `reason`
when it did not compile, the `commit` it made, and in `diagnostics`: the cases that
failed and why (time limit, crash, illegal answer), the lowest and highest case score,
the longest run time, every case's score, and per case M, ε, the N chosen and the
wrong answers E. An evaluation takes under a minute.
"""

LOCAL_SCHEMA = {"description": str}


def _git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)


def local_fn(args):
    cwd = os.environ["AGENT_CWD"]
    description = " ".join(str(args.get("description", "")).split()) or "experiment"

    try:
        proc = subprocess.run(
            ["python3", os.path.join(EVAL_DIR, "evaluate.py"),
             os.path.join(cwd, "main.cpp"), os.path.join(EVAL_DIR, "public", "in")],
            capture_output=True, text=True, timeout=EVAL_TIMEOUT)
        lines = [l for l in proc.stdout.splitlines() if l.strip().startswith("{")]
        out = json.loads(lines[-1]) if lines else {
            "status": "error", "error": (proc.stderr or "no output")[-500:]}
    except subprocess.TimeoutExpired:
        out = {"status": "error", "error": f"evaluation ran past {EVAL_TIMEOUT}s"}

    if out.get("status") == "success":
        score, reason = round(out["mean_score"], 2), None
        failed = f", {out['failed_cases']} case(s) failed" if out["failed_cases"] else ""
        message = f"{description} | score = {score}{failed}"
    else:
        score = None
        reason = out.get("status", "error")
        message = f"{description} | failed: {reason}"

    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "--allow-empty", "-m", message)
    commit = _git(cwd, "rev-parse", "--short", "HEAD").stdout.strip()
    return {"args": args, "score": score, "reason": reason, "commit": commit,
            "diagnostics": out, **({"scores": {"score": score}} if score is not None else {})}
