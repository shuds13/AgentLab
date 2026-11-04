# example-wind-tunnel

An agent looks for the shape that air drags on least, in a wind tunnel simulated on
Polaris GPUs. Every shape has the same area, so only its form can lower the drag: a
teardrop beats a circle, and a needle has too much surface. Each job returns the drag
and a picture of the airflow, which you can watch improve in the run's journal.

## What a job does

`task.py` sends a shape (its radius at evenly spaced angles) to a GPU on Polaris.
`sim.py` runs there: a lattice Boltzmann simulation of the tunnel in PyTorch, at a
Reynolds number of 100, where a blunt shape sheds a trail of vortices. It returns the
drag coefficient, how strongly the shape sheds, and `flow.png`, the air speed around the
shape. The picture comes back inside the job's result and is saved under the
workspace's `outputs/`. With `save_field` the full velocity field stays on Polaris, for
the agent to fetch with Globus Transfer if it is set up (docs/globus_transfer.md).

A job takes one GPU: about 6 s at the coarsest resolution, 45 s at 80 and 5.5 minutes
at 160, the finest, on Polaris's A100s. Four run at once on a node of the debug queue.
`sim.py` runs on its own too: `python sim.py args.json`, on a GPU or, slowly, a CPU.

## Running it

Polaris access in `users/<you>/polaris.json`, copied from `users/user1/polaris.json`:
the account to charge and a work directory. The template's endpoint is ALCF's facility
endpoint, which needs nothing installed; with your own, run `PROFILE=own ./run.sh`.
Jobs use Polaris's `module load conda` for PyTorch.

```
./run.sh                 one agent
./run_resources.sh       the same, with a resource agent choosing between the debug
                         and debug-scaling queues from what IRI reports (docs/iri.md)
```

The run is held to 40 jobs, two hours and 3 node-hours on Polaris (`run.sh`,
`campaign.json`). Another GPU system needs its own `system` in `campaign.json` and a
`worker_setup` that provides PyTorch.

## A first run

One agent on Polaris, 29 jobs in about 25 minutes. It measured a circle, tried
ellipses, teardrops and airfoil shapes, and confirmed the leaders at finer resolutions.
The best was an ellipse 6 times longer than it is tall, aligned with the flow: drag
coefficient 0.790 against the circle's 1.625 at the finest resolution, 51% less, with
the flow attached to its tail and a thin steady wake where the circle sheds a vortex
street. Longer than about 8:1, friction makes drag rise again.
