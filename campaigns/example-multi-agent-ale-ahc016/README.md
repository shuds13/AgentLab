# Multi-agent example: AHC016 (Graphorean)

A coordinator agent launches worker agents on AtCoder Heuristic Contest 016, from
[ALE-Bench](https://github.com/SakanaAI/ALE-Bench). Explorers each try one approach
from scratch; refiners continue a promising explorer's conversation and improve its
solution. Every worker has its own git branch, and all share one evaluation budget.
Everything runs on the local machine.

The design follows SwarmResearch ([Virk et al., arXiv:2607.02807](https://arxiv.org/abs/2607.02807)),
which reports this problem among its ALE-Bench tasks: a performance rating of 2138 with
Opus 4.6 at $50 per task.

Needs g++ (C++20) and Rust's `cargo`. The first run downloads the problem data and
builds AtCoder's tools.

## Run

```
./run.sh
```

Watch it with `bin/watch.sh`. The workspace is `workspace/<campaign>-<mode>-<model>-<trial>/`;
the best solution is the highest-scoring commit in its `repo/`, also named in `LOGBOOK.md`.

## Variants

| setting | default | |
|---|---|---|
| `MODE` | `directed` | `directed`: the coordinator assigns each explorer an approach (`director.md`). `undirected`: explorers choose their own (`shepherd.md`) |
| `MODEL` | `sonnet` | the coordinator's model |
| `EXPLORER_MODEL` | `opus` | explorers' model |
| `REFINER_MODEL` | `sonnet` | refiners' model |
| `EVALUATIONS` | `80` | evaluations all workers share; an explorer gets 8, a refiner 10 |
| `WORKERS` | `12` | the most workers the coordinator may launch |
| `LOCAL_MAX_CONCURRENT` | `3` | workers running at once |
| `MAX_RUNTIME` | `7200` | the coordinator's wallclock limit, in seconds |
| `TRIAL` | `1` | a new number gives a fresh workspace |
| `WORKER_CPUS`, `EVAL_CPUS` | `0-7`, `8-15` | cores for the agents and for evaluations |

For example: `MODE=undirected MODEL=opus TRIAL=2 ./run.sh`

## Scoring

Agents see the mean score over 50 public test cases (`evaluate.py`). Afterwards,
`hidden_score.py <main.cpp>`, run from `workspace/.<campaign>-eval/`, scores a
solution on the 200 hidden cases and gives its rank among the contest's 1,117
entrants and its AtCoder performance rating.

## Tested

October 2026, with the defaults (coordinator Sonnet 5.5, explorers Opus 5.5, refiners
Sonnet 5.5, 80 evaluations). Each coordinator ended the run itself.

| mode | best public score | hidden rank (of 1,117) | performance | evaluations used | workers | cost |
|---|---|---|---|---|---|---|
| directed | 35.4M | 41 | 2337 | 34 | 6 | $9.20 |
| undirected | 38.4M | 27 | 2480 | 27 | 5 | $8.64 |
