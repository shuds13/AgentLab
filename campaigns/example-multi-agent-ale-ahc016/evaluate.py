"""Score a C++ solution to AHC016 on a set of test inputs, as AtCoder would: compile it
with g++ -std=c++20 -O2 and run it on each input within the 5-second limit, under
AtCoder's official tester, which plays the judge's side of the exchange and scores it.
A case that fails to compile, runs over the limit, crashes or produces an illegal answer
scores 0.

Usage: evaluate.py <main.cpp> <inputs dir> [--cases N]   (prints one JSON object)

Evaluations take a lock in this directory and run on EVAL_CPUS, one case per core, so
two evaluations never compete for time and every case gets a full core.
"""

import concurrent.futures
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTER = os.path.join(HERE, "ahc016", "tools", "target", "release", "tester")
TIME_LIMIT = 5.0
EVAL_CPUS = os.environ.get("EVAL_CPUS", "8-15")


def cpus(spec):
    out = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        out += range(int(lo), int(hi or lo) + 1)
    return out


def run_case(binary, path, cpu):
    """Run one case on one core under the tester; return (score, error, seconds, N, E):
    the graph size the solution chose and its wrong predictions, None when unknown."""
    with open(path) as inp, tempfile.NamedTemporaryFile("w+", suffix=".txt") as out:
        start = time.monotonic()
        # The solution's own stderr is discarded, so only the tester reports a score. Its
        # own session, so a case over the limit is killed with everything it started.
        proc = subprocess.Popen(["taskset", "-c", str(cpu), TESTER, "sh", "-c",
                                 f"exec {binary} 2>/dev/null"], stdin=inp, stdout=out,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            _, err = proc.communicate(timeout=TIME_LIMIT + 1.0)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            return 0, "time limit exceeded", time.monotonic() - start, None, None
        seconds = time.monotonic() - start
        n = re.search(r"^N = (\d+)", err, re.M)
        e = re.search(r"^E = (\d+)", err, re.M)
        n, e = (int(n.group(1)) if n else None), (int(e.group(1)) if e else None)
        if seconds > TIME_LIMIT:
            return 0, f"time limit exceeded ({seconds:.2f}s)", seconds, n, e
        found = re.search(r"^Score = (\d+)", err, re.M)
        if not found or proc.returncode != 0 or int(found.group(1)) == 0:
            message = " ".join(err.split())[-200:]
            return 0, f"wrong answer: {message}", seconds, n, e
        return int(found.group(1)), None, seconds, n, e


def case_params(path):
    """M and epsilon from the first line of an input; the rest is the judge's secret."""
    with open(path) as f:
        m, eps = f.readline().split()[:2]
    return {"M": int(m), "eps": float(eps)}


def main():
    source, inputs = sys.argv[1], sys.argv[2]
    cases = sorted(os.listdir(inputs))
    if "--cases" in sys.argv:
        cases = cases[:int(sys.argv[sys.argv.index("--cases") + 1])]
    with tempfile.TemporaryDirectory() as tmp:
        binary = os.path.join(tmp, "main")
        build = subprocess.run(["g++", "-std=c++20", "-O2", "-o", binary, source],
                               capture_output=True, text=True)
        if build.returncode != 0:
            print(json.dumps({"status": "compile error",
                              "error": build.stderr[-3000:]}))
            return
        with open(os.path.join(HERE, ".evaluation.lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            pool = cpus(EVAL_CPUS)
            with concurrent.futures.ThreadPoolExecutor(len(pool)) as ex:
                futures = {}
                for k, case in enumerate(cases):
                    futures[case] = ex.submit(run_case, binary,
                                              os.path.join(inputs, case), pool[k % len(pool)])
                results = {case: f.result() for case, f in futures.items()}
    scores = [results[c][0] for c in cases]
    failures = {c: results[c][1] for c in cases if results[c][1]}
    print(json.dumps({
        "status": "success",
        "mean_score": sum(scores) / len(scores),
        "total_score": sum(scores),
        "cases": len(cases),
        "failed_cases": len(failures),
        "failures": dict(list(failures.items())[:10]),
        "min_score": min(scores),
        "max_score": max(scores),
        "max_seconds": round(max(results[c][2] for c in cases), 3),
        "scores": dict(zip(cases, scores)),
        # Per case: the given M and epsilon, the graph size N the solution chose and its
        # wrong predictions E out of 100.
        "cases_detail": {c: {**case_params(os.path.join(inputs, c)), "N": results[c][3],
                             "E": results[c][4]} for c in cases},
    }))


if __name__ == "__main__":
    main()
