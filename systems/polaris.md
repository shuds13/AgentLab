# ALCF Polaris

Facts about Polaris for anyone sizing work for it. Settings live in `polaris.json` beside
this file. Queue rules change; check the docs and `qstat -Qf <queue>` before relying on
them.

- Docs: https://docs.alcf.anl.gov/polaris/
- Queues and job rules: https://docs.alcf.anl.gov/polaris/running-jobs/

## Nodes

About 560 nodes, each with 4 NVIDIA A100 GPUs,
64 hardware threads (32 cores, 2 threads each) and about 512 GB of memory.

## Queues

From the docs:

| queue | nodes per job | walltime | limits |
|---|---|---|---|
| debug | 1–2 | 5 min–1 h | 24 nodes for the queue at once: 8 reserved for it, 16 more when free |
| debug-scaling | 1–10 | 5 min–1 h | 1 job running or queued per user |
| prod | 10–496 | 5 min–24 h | routes by size: small 10–24 nodes up to 3 h, medium 25–99 up to 6 h, large 100–496 up to 24 h; 10 running and 100 queued per project |
| preemptable | 1–10 | 5 min–72 h | 20 jobs per project; killed without warning when demand jobs need the nodes (`#PBS -r y` to rerun) |
| demand | 1–56 | 5 min–1 h | by request only |
| capacity | 1–4 | 5 min–168 h | 32 nodes for the queue; 2 jobs queued or running and 1 running per user |

Enforced limits the docs do not state (`qstat -Qf debug`):

- **debug:** 1 running job per user. A second job waits in the queue, so two blocks on
  debug do not run at the same time.
- **debug, queued:** a second queued job from the same user was refused at submission.
  A Globus Compute endpoint with `max_blocks` above 1 on debug then retries the
  submission every few seconds until the first block starts.
