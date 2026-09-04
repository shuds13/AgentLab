# Endpoint templates

Working starting points for the Globus Compute endpoint that runs your jobs. Copy
the closer one and edit the marked lines.

| | Scheduler | Launcher | Tested on |
|---|---|---|---|
| `aurora_pbs/` | PBS Pro | SimpleLauncher | ALCF Aurora |
| `polaris_pbs/` | PBS Pro | SimpleLauncher | ALCF Polaris |
| `perlmutter_slurm/` | Slurm | SimpleLauncher | NERSC Perlmutter GPU nodes |
| `slurm/` | Slurm | SimpleLauncher | CPU nodes; GPU variant noted in the file |

Perlmutter has its own file because NERSC selects nodes by `constraint` and the queue
by `qos`, where the generic Slurm template uses `partition`.

`SimpleLauncher` gives one worker per allocation, and the job's own launcher spans the
nodes. For many independent single-device tasks, use `MpiExecLauncher` with
`available_accelerators` — see the note in `polaris_pbs/`.

Setting one up start to finish: `docs/setup.md`.

## How the pieces fit together

The endpoint lives on the **compute system**, not with the agent. Its template
decides how a job reaches the batch scheduler, and it takes `{{ variables }}` that
the agent fills in per job:

```
users/<you>/<system>.json                    the endpoint's template
  buckets.small.user_config      ---->     user_config_template.yaml.j2
    account: MYPROJECT                       account: {{ account }}
    queue: debug-scaling                     queue: {{ queue }}
    num_nodes: 6                             nodes_per_block: {{ num_nodes }}
```

So the keys you put in `user_config` are not scheduler keys — they are whatever
your template declares. If you add `{{ constraint }}` to the template, you can
then pass `constraint` from `config.json`. Keep the two in step.

## Setting one up

On the compute system:

```
pip install globus-compute-endpoint
globus-compute-endpoint configure my-endpoint
```

That creates `~/.globus_compute/my-endpoint/`. Copy the closer of the two
templates over `user_config_template.yaml.j2` there, then edit:

- **`account`** — your project/allocation.
- **`worker_init`** — the environment your job needs (module loads, conda, venv).
  The worker starts in a bare shell, so anything your job assumes must be
  activated here.
- **PBS only:** `scheduler_options` declares the filesystems you touch. ALCF
  requires this; other PBS sites may not.
- **Slurm only:** the file ends with a commented GPU variant if you need one.

Then:

```
globus-compute-endpoint start my-endpoint
globus-compute-endpoint list          # note the UUID -- it goes in config.json
```

## Check it before involving the agent

A broken endpoint looks exactly like an agent that submits and never hears back,
so confirm it independently first:

```python
from globus_compute_sdk import Executor


def hello():
    import socket

    return f"ran on {socket.gethostname()}"


ex = Executor(
    endpoint_id="your-uuid",
    user_endpoint_config={
        "account": "MYPROJECT",
        "queue": "debug",
        "num_nodes": 1,
        "walltime": "00:10:00",
    },
)
print(ex.submit(hello).result())
```

If that returns a hostname, the endpoint, the template and your scheduler
arguments all work, and anything that fails afterwards is the task or the agent.

## Limits: the endpoint is where you enforce them

The endpoint decides what reaches the scheduler, so it is where ceilings belong.
`max_concurrent` in `config.json` is agent-side; a limit in the template holds
regardless of what any agent asks for.

- **`max_blocks`** — caps how many batch jobs this endpoint has outstanding at
  once, whatever the agent submits. Extra tasks queue inside the existing blocks.
  Both templates ship with `max_blocks: 1`.
- **`nodes_per_block`** — how big each job is.
- **`walltime`** — how long each job may run.

Note that `{{ walltime|default("00:30:00") }}` is a **fallback, not a ceiling**: it
applies only when the agent supplies nothing. To make a value an enforced limit,
either hard-code it:

```yaml
    walltime: "01:00:00"           # agents cannot ask for longer
```

or clamp what is passed:

```yaml
    nodes_per_block: {{ [num_nodes|default(1), 8]|min }}     # never more than 8
```

## Notes worth knowing

- **Idle timeout.** The PBS template sets `idle_heartbeats_soft: 10`, so ~5 idle
  minutes with no outstanding tasks releases the nodes. It deliberately does not
  set `idle_heartbeats_hard`, which shuts down when tasks exist but are not
  moving — indistinguishable from a job sitting in a long queue, and it would kill
  legitimately queued work.
- **Launcher.** The PBS template uses `SimpleLauncher` because the example task
  runs its own `mpiexec` across the allocation. If your task is a plain serial
  function and you want the worker distributed for you, use `MpiExecLauncher`
  instead. Two nested MPI launches will not work.
- **One template, many shapes.** You do not need an endpoint per job size. Buckets
  in `config.json` pass different `num_nodes`/`walltime` to the same endpoint.
