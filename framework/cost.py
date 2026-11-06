"""What a workspace's runs consumed: tokens per model, and dollars where they are known.

    python3 framework/cost.py <workspace> [--json]

<workspace> is a campaign's workspace under workspace/, by name or path; a group's
directory covers every workspace in it. Each run records its cost in runs/<id>/meta.json
whether or not it shows it (SHOW_COST). A forked run's figures are its own, without the
session it forked, so the runs add up. Dollars are only those Claude Code priced for a
Claude model reached directly; other models show their tokens and no dollars.
"""

import glob
import json
import os
import sys

LAB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _workspaces(arg):
    """The given directory and the workspaces in it, each with its name below it."""
    path = arg if os.path.isdir(arg) else os.path.join(LAB_DIR, "workspace", arg)
    if not os.path.isdir(path):
        sys.exit(f"no workspace {arg!r}")
    if os.path.isdir(os.path.join(path, "runs")):
        return [(path, "")]
    found = sorted(os.path.dirname(r) for r in glob.glob(os.path.join(path, "*", "runs")))
    if not found:
        sys.exit(f"{path} has no runs/, nor any workspace in it")
    return [(ws, os.path.basename(ws)) for ws in found]


def runs(arg):
    """One row per run that recorded a cost, oldest first."""
    out = []
    for ws, name in _workspaces(arg):
        for m in glob.glob(os.path.join(ws, "runs", "*", "meta.json")):
            try:
                with open(m) as f:
                    meta = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            models = meta.get("cost_models") or []
            if not models:
                continue
            out.append({"workspace": name,
                        "run": os.path.basename(os.path.dirname(m)),
                        "agent": meta.get("branch") or meta.get("kind") or "top",
                        "started_at": meta.get("started_at") or "",
                        "models": models})
    out.sort(key=lambda r: r["started_at"])
    return out


def totals(rows):
    """Per model: tokens and dollars summed over the rows, and how many runs had no
    dollar figure for it."""
    t = {}
    for r in rows:
        for c in r["models"]:
            e = t.setdefault(c["model"], {"input_tokens": 0, "output_tokens": 0,
                                          "usd": None, "runs_unpriced": 0})
            e["input_tokens"] += c.get("input_tokens") or 0
            e["output_tokens"] += c.get("output_tokens") or 0
            if c.get("usd") is None:
                e["runs_unpriced"] += 1
            else:
                e["usd"] = (e["usd"] or 0.0) + c["usd"]
    return t


def _usd(v):
    return "—" if v is None else f"${v:,.2f}"


def main():
    args = [a for a in sys.argv[1:] if a != "--json"]
    if len(args) != 1:
        sys.exit(__doc__.strip().splitlines()[2].strip())
    rows = runs(args[0])
    tot = totals(rows)
    if "--json" in sys.argv:
        json.dump({"runs": rows, "models": tot,
                   "usd": round(sum(e["usd"] or 0 for e in tot.values()), 4)}, sys.stdout,
                  indent=1)
        print()
        return
    for r in rows:
        name = f"{r['workspace']}/{r['agent']}" if r["workspace"] else r["agent"]
        for c in r["models"]:
            print(f"{name:32} {c['model']:28} {c.get('input_tokens') or 0:>14,} in "
                  f"{c.get('output_tokens') or 0:>11,} out {_usd(c.get('usd')):>10}  {r['run']}")
    print()
    for model, e in sorted(tot.items()):
        note = f"  ({e['runs_unpriced']} runs without a price)" if e["runs_unpriced"] else ""
        print(f"{'total':32} {model:28} {e['input_tokens']:>14,} in "
              f"{e['output_tokens']:>11,} out {_usd(e['usd']):>10}{note}")
    priced = [e["usd"] for e in tot.values() if e["usd"] is not None]
    print(f"{'total':32} {'all models':28} {'':>14}    {'':>11}     "
          f"{_usd(sum(priced) if priced else None):>10}")


if __name__ == "__main__":
    main()
