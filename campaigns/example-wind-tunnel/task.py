"""One job: put a shape in a 2D wind tunnel on a GPU and measure its drag.

The tunnel is a lattice Boltzmann simulation (D2Q9) in PyTorch: air flows in from the
left at a fixed speed, past the shape, and out on the right. The shape is given as its
radius at evenly spaced angles around its centre, smoothed into a closed outline and
scaled to a fixed area, so every shape has the same amount of "stuff" and only its form
differs. The job returns the drag coefficient and a picture of the flow.

remote_fn is shipped to the compute system by source. The simulation itself is sim.py,
sent with each job (REMOTE_FILES), written to the job's directory there and run with the
system's own Python environment (target["worker_setup"], e.g. Polaris's
`module load conda`), which has PyTorch for the GPU.
"""

REMOTE_FILES = ["sim.py"]

JOB_DESC = """
Put one shape in the wind tunnel and measure its drag.

Air flows from left to right past the shape. The shape is given by `radii`: its distance
from its centre at evenly spaced angles, starting pointing DOWNSTREAM (angle 0, the
tail, to the right) and going anticlockwise, so with 8 radii the 5th points UPSTREAM
(the nose, facing the oncoming air). The outline is smoothed through these points and
then scaled so the shape's area always equals that of a circle of diameter D, the
tunnel's unit of length. Only the proportions of `radii` matter, not their size. Give
4 to 32 positive numbers; [1, 1, 1, 1] is a circle.

Parameters:
  radii       the outline, as above.
  resolution  cells across D: 20, 40, 80 or 160. Finer is more accurate and slower:
              on a Polaris A100 about 6 s at 20, 12 s at 40, 45 s at 80 and 5.5 minutes
              at 160. Shapes compared against each other should be at the same
              resolution.
  save_field  true to keep the final velocity field on the compute system (a .npz in
              the job's directory), to fetch with the transfer tool. Default false.

Returns:
  cd          the drag coefficient, averaged over the last third of the run: the number
              to MINIMISE. A circle is about 1.4 at this Reynolds number.
  cd_std      how much the drag swings over that time; large when the shape sheds
              vortices.
  cl_rms      the swing in sideways force (lift), also from vortex shedding.
  strouhal    the shedding frequency, dimensionless; null if the flow is steady.
  length, height   the shape's extent along and across the flow, in units of D.
  files       flow.png, a picture of the air speed around the shape at the end of the
              run (dark slow, bright fast, the shape in black), saved in the workspace.
              Look at it: it shows where the flow separates and how big the wake is.
  device, wall_seconds   what it ran on and how long it took.

A shape too long or too tall to fit in the tunnel is refused with an error.
"""

JOB_SCHEMA = {"radii": list, "resolution": int, "save_field": bool}

RESOLUTIONS = (20, 40, 80, 160)


def job_key(args):
    r = args.get("radii") or []
    try:
        top = max(float(x) for x in r) or 1.0
        shape = ",".join(f"{float(x) / top:.4g}" for x in r)
    except (TypeError, ValueError):
        shape = str(r)
    return f"shape[{shape}]/res{args.get('resolution', 40)}"


def bucket_for(args):
    return "gpu"


def remote_fn(args, target):
    """Run one shape on one GPU of the node. Shipped by source: all imports are here."""
    import base64
    import fcntl
    import json
    import os
    import shlex
    import subprocess
    import time

    radii = args.get("radii")
    if not isinstance(radii, list) or not 4 <= len(radii) <= 32:
        return {"error": "radii must be a list of 4 to 32 positive numbers", "args": args}
    try:
        radii = [float(x) for x in radii]
    except (TypeError, ValueError):
        return {"error": f"radii must be numbers, got {radii!r}", "args": args}
    if min(radii) <= 0:
        return {"error": "every radius must be positive", "args": args}
    resolution = int(args.get("resolution", 40) or 40)
    if resolution not in (20, 40, 80, 160):
        return {"error": f"resolution must be 20, 40, 80 or 160, got {resolution}",
                "args": args}

    stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{os.getpid()}"
    job_dir = os.path.join(target["work_dir"], "jobs", stamp)
    os.makedirs(job_dir, exist_ok=True)
    with open(os.path.join(job_dir, "sim.py"), "w") as f:
        f.write(target["files"]["sim.py"])
    spec = {"radii": radii, "resolution": resolution,
            "reynolds": float(target.get("reynolds", 100)),
            "save_field": bool(args.get("save_field")), "out_dir": job_dir}
    with open(os.path.join(job_dir, "args.json"), "w") as f:
        json.dump(spec, f)

    # One GPU per job. An endpoint that pins one per worker sets CUDA_VISIBLE_DEVICES;
    # otherwise the job takes the first GPU on the node no other job holds, by a lock
    # file kept for as long as it runs.
    env = dict(os.environ)
    lock = None
    if not env.get("CUDA_VISIBLE_DEVICES"):
        for i in range(int(target.get("gpus_per_node", 4))):
            fh = open(f"/tmp/agentlab_gpu_{os.getuid()}_{i}.lock", "w")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fh.close()
                continue
            lock, env["CUDA_VISIBLE_DEVICES"] = fh, str(i)
            break
        else:
            return {"error": "every GPU on this node is taken by another job", "args": args,
                    "retry_after": 30}
    setup = target.get("worker_setup", "")
    cmd = (f"{setup} && " if setup else "") + \
        f"python {shlex.quote(os.path.join(job_dir, 'sim.py'))} " \
        f"{shlex.quote(os.path.join(job_dir, 'args.json'))}"
    try:
        p = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, env=env,
                           cwd=job_dir, timeout=int(target.get("timeout", 3000)))
    except subprocess.TimeoutExpired:
        return {"error": "the simulation ran past its time limit", "args": args,
                "job_dir": job_dir}
    finally:
        if lock:
            lock.close()
    with open(os.path.join(job_dir, "log.txt"), "w") as f:
        f.write(p.stdout + "\n--- stderr ---\n" + p.stderr)
    lines = [l for l in p.stdout.splitlines() if l.startswith("{")]
    if p.returncode or not lines:
        return {"error": f"the simulation failed (exit {p.returncode})",
                "stderr": p.stderr[-1500:], "args": args, "job_dir": job_dir}
    res = json.loads(lines[-1])
    if "error" in res:
        return {**res, "args": args}
    png = os.path.join(job_dir, "flow.png")
    if os.path.exists(png):
        with open(png, "rb") as f:
            res["files"] = {"flow.png": base64.b64encode(f.read()).decode()}
    res.update(args={"radii": radii, "resolution": resolution}, job_dir=job_dir,
               gpu=env.get("CUDA_VISIBLE_DEVICES"))
    return res

