#!/bin/bash
# Build the evaluator and the search repo for AHC016 (Graphorean) from ALE-Bench.
# run.sh calls it.
#
#   $EVAL_DIR/        shared by every run, outside any agent's working directory
#     ahc016/         the ALE-Bench problem data: statement, AtCoder's tools, standings
#     public/in/      the 50 public test inputs (seeds 0-49): what agents are scored on
#     private/in/     the 200 hidden test inputs the human standings are computed on
#   $WORKSPACE_DIR/
#     repo/           git repo, branch master: main.cpp (a minimal valid solution) and
#                     problem.md. Each agent gets a worktree under .worktrees/.
#
# Needs g++ with C++20 and Rust's cargo (for AtCoder's tools).
set -euo pipefail
cd "$(dirname "$0")"

CAMPAIGN="$(basename "$PWD")"
LAB_DIR="$(cd ../.. && pwd)"
WS="${WORKSPACE_DIR:-$LAB_DIR/workspace/$CAMPAIGN}"
EV="${EVAL_DIR:?EVAL_DIR names where the evaluator lives}"
CARGO="${CARGO:-$HOME/.cargo/bin/cargo}"
DATA_URL=https://huggingface.co/datasets/SakanaAI/ALE-Bench/resolve/main/ahc016.zip
DATA_SHA256=6be902402ee316cdfe59dbd0e6045507ff31d0dfd14bc68bc6f1258366ca54cb

mkdir -p "$WS" "$EV"
if [ ! -d "$EV/ahc016" ]; then
    echo "[setup] fetching ALE-Bench ahc016"
    curl -sSfL -o "$EV/ahc016.zip" "$DATA_URL"
    echo "$DATA_SHA256  $EV/ahc016.zip" | sha256sum -c --quiet
    (cd "$EV" && unzip -q ahc016.zip)
fi
if [ ! -x "$EV/ahc016/tools/target/release/tester" ]; then
    echo "[setup] building AtCoder's tools"
    (cd "$EV/ahc016/tools" && "$CARGO" build -q --release)
fi
if [ ! -d "$EV/public/in" ]; then
    echo "[setup] test inputs"
    mkdir -p "$EV/public" "$EV/private"
    seq 0 49 > "$EV/public/seeds.txt"
    python3 -c "import json, sys; print('\n'.join(map(str, json.load(open(sys.argv[1]))['seeds']['private_lite'])))" \
        "$EV/ahc016/data.json" > "$EV/private/seeds.txt"
    (cd "$EV/public" && "$EV/ahc016/tools/target/release/gen" seeds.txt > /dev/null)
    (cd "$EV/private" && "$EV/ahc016/tools/target/release/gen" seeds.txt > /dev/null)
fi
cp evaluate.py hidden_score.py "$EV/"

if [ -e "$WS/repo" ]; then
    echo "[setup] $WS/repo exists"
    exit 0
fi
echo "[setup] search repo"
mkdir -p "$WS/repo"
cp seed.cpp "$WS/repo/main.cpp"
cp problem.md "$WS/repo/problem.md"
printf '__pycache__/\n.worktrees/\nscratch/\n' > "$WS/repo/.gitignore"
git -C "$WS/repo" init -q -b master
git -C "$WS/repo" config user.name agentlab
git -C "$WS/repo" config user.email agentlab@localhost
git -C "$WS/repo" add -A
git -C "$WS/repo" commit -q -m "Minimal valid solution"
echo "[setup] done: $WS/repo"
