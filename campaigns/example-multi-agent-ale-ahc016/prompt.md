# AHC016: Graphorean (AtCoder Heuristic Contest 016)

The problem is in `problem.md` in the search repo, and is restated in each agent's
working directory: choose a graph size N and M graphs on N vertices, then identify,
100 times, which of them a noisy copy with shuffled vertices came from. A test case
scores round(10^9 × 0.9^E / N), where E is the number of wrong answers, so higher is
better: fewer mistakes and a smaller N both raise it.

## The solution

A solution is one C++ file, `main.cpp`. It reads M and ε, writes N and its M graphs,
then answers the 100 queries one at a time, flushing after each answer. It is compiled
with `g++ -std=c++20 -O2` and must finish each case within 5 seconds. The search starts
from a minimal valid solution: N = 4, every graph empty, every answer 0.

## Evaluation

An agent that edits the solution has file tools and its `submit_local` tool, and no
shell. `submit_local` compiles `main.cpp`, runs it on 50 public test cases under
AtCoder's official tester, and commits the working directory with the result. The score
is the mean over the 50 cases. A case that runs over the time limit, crashes or gives an
illegal answer scores 0, and the evaluation reports which cases failed and why; a compile
error is reported with the compiler's message. For each case it also reports M, ε, the N
the solution chose and its wrong answers E. Notes are saved with each evaluation and
again when the agent finishes.

Case scores span several orders of magnitude: a case with ε = 0 can be solved with a
small N, while a noisy one needs a large N and still makes mistakes. The mean is
dominated by the low-noise cases. The contest itself ranked each case relative to the
best anyone achieved on it, so the noisy cases counted as much as the easy ones.

For reference: the minimal solution scores about 8,900 per case.

## The search repo

The work lives in `repo/` in the workspace, a git repository whose `master` branch
holds the minimal solution and `problem.md`. Each agent has its own branch, checked out
in its own worktree under `repo/.worktrees/`. Every evaluation is committed with its
result, as `{experiment description} | score = {score}`.
