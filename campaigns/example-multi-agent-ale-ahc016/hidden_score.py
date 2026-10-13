"""Score a solution on AHC016's 200 hidden test cases and place it among the human
contestants, as ALE-Bench does. AHC016 ranks by relative score: on each case a
contestant gets 10^9 x their score / the best score anyone got on it, and the standings
order the sums. Adding this solution can raise a case's best, so every contestant's sum
is recomputed with it included before it is ranked. Also gives the AtCoder performance
rating for that rank.

Usage: hidden_score.py <main.cpp>   (prints one JSON object)

Agents never see these cases or this result; it is for comparing runs afterwards.
"""

import csv
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ahc016")
RELATIVE_MAX = 10**9


def relative(score, best):
    """10^9 x score / best, rounded half up as AtCoder does; 0 for a rejected case."""
    if score < 0 or not best:
        return 0
    q, r = divmod(RELATIVE_MAX * score, best)
    return q + (1 if 2 * r >= best else 0)


def main():
    out = subprocess.run([sys.executable, os.path.join(HERE, "evaluate.py"), sys.argv[1],
                          os.path.join(HERE, "private", "in")],
                         capture_output=True, text=True, check=True)
    result = json.loads(out.stdout)
    if result["status"] != "success":
        print(json.dumps(result))
        return
    cases = sorted(result["scores"])
    # A failed case is rejected (-1) in the standings, not a score of 0.
    mine = [result["scores"][c] if result["scores"][c] > 0 else -1 for c in cases]
    # One row per contestant, one column per hidden case, in the order of the inputs.
    with open(os.path.join(DATA, "relative_results_lite.csv")) as f:
        rows = [[int(v) for v in r] for r in list(csv.reader(f))[1:]]
    if len(rows[0]) != len(mine):
        raise SystemExit(f"{len(mine)} cases scored, the standings have {len(rows[0])}")
    best = [max([s for s in col if s >= 0] + ([m] if m >= 0 else []), default=0)
            for col, m in zip(zip(*rows), mine)]
    my_total = sum(relative(m, b) for m, b in zip(mine, best))
    others = [sum(relative(s, b) for s, b in zip(row, best)) for row in rows]
    above = sum(1 for t in others if t > my_total)
    tied = sum(1 for t in others if t == my_total)
    rank = above + 1
    # Ties share the average of their places, as ALE-Bench does for the performance.
    rank_for_performance = rank + tied / 2
    with open(os.path.join(DATA, "performance.csv")) as f:
        performance = {int(r["rank"]): int(r["performance"]) for r in csv.DictReader(f)}
    known = [r for r in performance if r <= rank_for_performance]
    print(json.dumps({
        "relative_total": my_total,
        "mean_score": result["mean_score"],
        "failed_cases": result["failed_cases"],
        "rank": rank,
        "contestants": len(rows),
        "top_percent": round(100 * rank / len(rows), 1),
        "performance": performance[max(known)] if known else None,
        "first_place_relative_total": max(others),
    }))


if __name__ == "__main__":
    main()
