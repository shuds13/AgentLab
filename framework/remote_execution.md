# How remote work runs

This is how a task you submit reaches a compute node and comes back, through Globus
Compute. Read it before your first remote submission.

## The parts

- **Task** — one call to `submit_job`. The tools call it a job (`submit_job`, `job_id`,
  `get_completed_jobs`); it is one unit of work, and a task is what this document means.
- **Batch job** — an allocation the endpoint requests from the scheduler (PBS or Slurm).
  Globus Compute and Parsl call it a **block**. It has a node count (`nodes_per_block`)
  and a `walltime`, and it waits in the scheduler's queue like any batch job.
- **Worker** — the process inside a block that runs tasks. With the `SimpleLauncher` the
  endpoints here use, the workers run on the block's first node, and a task spans the
  rest of the allocation with its own `mpiexec` or `srun`. A block runs as many tasks at
  once as it has workers: `max_workers_per_node`, which is 1 unless the campaign sets it.
- **Endpoint** — the Globus Compute service on the compute system. It receives tasks,
  requests blocks, and hands each task to a free worker.
- **Bucket** — a named resource shape: queue, `nodes_per_block`, `walltime`,
  `max_blocks`, `max_concurrent`. The campaign's `campaign.json` sets them over the
  system's defaults, and this run's `meta.json` (under `runs/<run_id>/` in the workspace)
  lists each bucket's values as resolved. Each bucket has its own pool of blocks.
  `max_blocks` caps how many blocks the pool holds at once; `max_concurrent` caps how
  many of your tasks are in flight in it.

## What happens to a task

1. You submit it to a bucket. It runs on a free worker in one of the bucket's blocks,
   or waits for one; see "How many blocks a bucket opens" below.
2. The block waits in the scheduler queue, then starts. Its walltime starts counting now.
3. A free worker in the block runs the task. The result comes back through
   `get_completed_jobs`.
4. When the task finishes, the block stays up. It is held idle for up to 10 minutes,
   and a task submitted to the same bucket in that time runs in it.
5. When the block reaches its walltime, the scheduler ends it, and any task running in it
   ends too.

## How many blocks a bucket opens

Parsl opens a new block when the tasks waiting outnumber the slots it counts, up to
`max_blocks`. It counts `nodes_per_block × max_workers_per_node` slots per block, as if
every node ran workers. With `SimpleLauncher` only the first node does, so a block
really has `max_workers_per_node` slots.

- **`parallelism` at 1**, the default on this lab's templates and on facility
  endpoints: a second multi-node task in a bucket waits for the first and then runs in
  its block, with what is left of that block's walltime.
- **`parallelism` set to `nodes_per_block`** in the bucket (this lab's templates
  only): Parsl requests a block per task, up to `max_blocks`, and each starts with the
  full walltime. Whether a requested block runs or waits is up to the scheduler and its
  per-user job limits.

## Walltime belongs to the block

A block's walltime counts from when the block started, and covers every task it runs. A
task that lands in a block that has already run another task gets what is left of that
block's walltime, which can be much less than the bucket's `walltime`.

So a long task is safe in a fresh block and at risk in a reused one. A task lands in a
reused block when it is waiting in a bucket as another task there finishes, or is
submitted within 10 minutes of one finishing. The risk is highest when the task needs
most of a walltime. The time a block has already run is at least the
run time of the tasks it has run, where the task's result reports one.

A task ended this way returns no result. Its log and any files it wrote on the compute
system keep what it produced before it ended.

## When a task is lost: `ManagerLost`

`ManagerLost` in a task's error means the worker running it stopped reporting to the
endpoint. The traceback names the host. The causes are:

- **The block reached its walltime** — the most common cause. The task's run time plus
  what the block had run before it adds up to the bucket's walltime.
- **A node failed, or the worker's process was killed.** The task ended well short of
  the block's walltime.

Comparing how long the task ran with the bucket's walltime, and with what the block ran
before it, usually tells the two apart. Record which one it was, and the evidence.
