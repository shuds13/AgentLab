"""One job: measure a hidden response at one setting.

Each reading runs a random-walk Metropolis sampler against a standard normal target:
from the current point it proposes a move of size `setting`, accepts or rejects it on
the Metropolis rule, and does that a few million times. The response is the sampler's
cost per step -- the reciprocal of its mean squared jump. Small steps are nearly always
accepted but go nowhere; large ones travel far but are nearly always rejected. Both
waste work, so the cost has a single minimum at a step size in between. Neither the
minimum nor the shape around it is written down in this file; both come out of the run.

Only local_fn is defined, so this campaign needs no endpoint and no account.

The measurement is noisy because the chain is a finite random run, so two nearby
settings can come back in the wrong order. Averaging replicates narrows that, which is
what makes the difference between a claim the rows support and a claim that merely
sounds right.

A reading takes about a second of CPU, so a cycle of jobs costs seconds rather than an
afternoon.
"""

import math

STEPS = 3_000_000           # sampler steps in one reading; sets what a job costs

LOCAL_DESC = """
Measure the response of the system at one setting.

Returns `response` -- the number to MINIMISE -- for the `setting` you pass, along with
the reading's own estimate of its noise.

Parameters:
  setting    the dial to tune, a number from 0 to 10.
  replicates 1-9, default 1. Readings to average. The mean of several readings is a
             tighter estimate than one: the reported `noise_sd` falls as their number
             rises, and `readings` gives you the individual values.

The response is noisy, so two settings whose means differ by less than the noise are
not distinguishable from single readings. A reading takes about a second, and a job
costs that per replicate.
"""

LOCAL_SCHEMA = {"setting": float, "replicates": int}


def _reading(setting, rnd):
    """Run the sampler once at this step size and return its cost per step.

    The walker sits on a standard normal target. `jumped` accumulates the squared
    distance actually moved, which is the work the chain got done; rejected proposals
    add nothing to it but cost a step all the same.
    """
    x = rnd.gauss(0.0, 1.0)
    jumped = 0.0
    for _ in range(STEPS):
        proposal = x + setting * rnd.gauss(0.0, 1.0)
        # Metropolis: accept with probability target(proposal)/target(x), capped at 1.
        if math.log(rnd.random()) < 0.5 * (x * x - proposal * proposal):
            jumped += (proposal - x) ** 2
            x = proposal
    if jumped <= 0.0:
        return float("inf")                   # a chain that never moved: unusable
    return STEPS / jumped                     # cost per unit of distance covered


def local_fn(args):
    import random

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

    # Unseeded, so re-measuring the same point runs a fresh chain rather than handing
    # back the same number -- a replicate has to cost a job, as it would in life.
    rnd = random.Random()
    readings = [round(_reading(setting, rnd), 4) for _ in range(replicates)]

    mean = sum(readings) / len(readings)
    if len(readings) > 1:
        var = sum((r - mean) ** 2 for r in readings) / (len(readings) - 1)
        spread = math.sqrt(var / len(readings))
    else:
        spread = None                         # one reading cannot measure its own spread
    return {
        "args": {"setting": setting, "replicates": replicates},
        "response": round(mean, 4),
        "noise_sd": None if spread is None else round(spread, 4),
        "readings": readings,
        "diagnostics": {"replicates": replicates, "steps_per_reading": STEPS},
    }

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


JOB_DESC = LOCAL_DESC
JOB_SCHEMA = dict(LOCAL_SCHEMA)


def job_key(args):
    """What makes two remote jobs the same piece of work."""
    return f"setting{float(args.get('setting', 0)):g}/rep{int(args.get('replicates', 1) or 1)}"
