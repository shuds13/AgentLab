"""One job: measure a hidden response at one setting.

A stand-in for a real experiment, kept deliberately small: a job takes about a second,
so a cycle costs a minute rather than an afternoon. The response has a true shape and a
true optimum that the framework knows and the agent does not, so what the agent
concludes can be checked against something other than its own reasoning.

Only local_fn is defined, so this campaign needs no endpoint and no account.

The measurement is noisy, on purpose. One reading cannot separate two nearby settings,
which is what makes the difference between a claim the rows support and a claim that
merely sounds right.
"""

import math

# What the agent is looking for, and never sees. Kept here rather than hidden away so
# whoever runs this can check the agent's conclusion against the truth afterwards.
TRUE_OPTIMUM = 3.4  # where the response is genuinely lowest
TRUE_FLOOR = 12.0  # the response there
CURVATURE = 1.7  # how sharply it rises either side
ASYMMETRY = 0.35  # rises faster above the optimum than below
NOISE_SD = 0.6  # spread of one reading, so nearby settings overlap

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
not distinguishable from single readings. A job takes about a second per replicate.
"""

LOCAL_SCHEMA = {"setting": float, "replicates": int}


def _response(setting, rnd):
    """The true response, plus the noise a single reading carries."""
    offset = setting - TRUE_OPTIMUM
    rise = CURVATURE * offset * offset
    if offset > 0:
        rise += ASYMMETRY * offset * offset * offset  # steeper above the optimum
    return TRUE_FLOOR + rise + rnd.gauss(0.0, NOISE_SD)


def local_fn(args):
    import random
    import time

    try:
        setting = float(args.get("setting"))
    except (TypeError, ValueError):
        return {
            "error": f"setting must be a number, got {args.get('setting')!r}",
            "args": args,
        }
    replicates = int(args.get("replicates", 1) or 1)

    if not 0.0 <= setting <= 10.0:
        return {
            "error": f"setting must be between 0 and 10, got {setting}",
            "args": args,
        }
    if not 1 <= replicates <= 9:
        return {"error": f"replicates must be 1-9, got {replicates}", "args": args}

    # Seeded on the setting, so re-measuring the same point gives a fresh draw rather
    # than the same number back -- a replicate has to cost a job, as it would in life.
    rnd = random.Random()
    readings = []
    for _ in range(replicates):
        time.sleep(0.8)  # a job is work, not an instant lookup
        readings.append(round(_response(setting, rnd), 4))

    mean = sum(readings) / len(readings)
    spread = NOISE_SD / math.sqrt(len(readings))
    return {
        "args": {"setting": setting, "replicates": replicates},
        "response": round(mean, 4),
        "noise_sd": round(spread, 4),
        "readings": readings,
        "diagnostics": {"replicates": replicates, "single_reading_sd": NOISE_SD},
    }
