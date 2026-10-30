"""The job of task.py, taken on a remote system through Globus Compute: run_remote.sh
selects it with TASK_MODULE. The description, arguments and job key are task.py's."""

from task import LOCAL_DESC as JOB_DESC, LOCAL_SCHEMA, job_key  # noqa: F401

JOB_SCHEMA = dict(LOCAL_SCHEMA)


def remote_fn(args, target):
    """The same reading, run on a compute system through Globus Compute.

    Shipped BY SOURCE: it cannot see this module's imports or globals, so STEPS and
    the sampler are repeated inside the body. That is a requirement, not a style
    choice.
    """
    import math
    import random

    steps = 3_000_000
    try:
        setting = float(args.get("setting"))
    except (TypeError, ValueError):
        return {"error": f"setting must be a number, got {args.get('setting')!r}",
                "args": args}
    replicates = int(args.get("replicates", 1) or 1)

    if not 0.0 <= setting <= 10.0:
        return {"error": f"setting must be between 0 and 10, got {setting}", "args": args}
    if not 1 <= replicates <= 9:
        return {"error": f"replicates must be 1-9, got {replicates}", "args": args}

    rnd = random.Random()
    readings = []
    for _ in range(replicates):
        x = rnd.gauss(0.0, 1.0)
        jumped = 0.0
        for _ in range(steps):
            proposal = x + setting * rnd.gauss(0.0, 1.0)
            if math.log(rnd.random()) < 0.5 * (x * x - proposal * proposal):
                jumped += (proposal - x) ** 2
                x = proposal
        readings.append(round(steps / jumped, 4) if jumped > 0 else float("inf"))

    mean = sum(readings) / len(readings)
    if len(readings) > 1:
        var = sum((r - mean) ** 2 for r in readings) / (len(readings) - 1)
        spread = math.sqrt(var / len(readings))
    else:
        spread = None
    return {
        "args": {"setting": setting, "replicates": replicates},
        "response": round(mean, 4),
        "noise_sd": None if spread is None else round(spread, 4),
        "readings": readings,
        "diagnostics": {"replicates": replicates, "steps_per_reading": steps},
    }
