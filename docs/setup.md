# Setup

From a clone to a running agent.

## 1. Install

Python 3.10+. Claude campaigns also need the `claude` CLI on your PATH and authenticated (`claude --version`). OpenAI-compatible campaigns need an API key in the configured environment variable; `OPENAI_API_KEY` is the default.

```
pip install -r requirements.txt
```

The agent runs wherever you start it — a workstation, a login node, a VM. It does not
run on the compute system.

## 2. Get an endpoint running on the compute system

The agent submits work through Globus Compute, so the compute system needs an endpoint.
Do this on that machine, over ssh.

**Use a Python that is new enough.** On many HPC systems the bare `python3` is the OS
one and can be years old. Check before creating anything:

```
python3 -V
```

If your application comes from a module, load it first and build the venv on top of it
with `--system-site-packages`, so the endpoint's workers can see the application:

```
module load <your-module> && python3 -m venv --system-site-packages ~/venvs/agentlab && source ~/venvs/agentlab/bin/activate && pip install -U globus-compute-endpoint
```

Confirm the venv can see both the endpoint command and your application:

```
which globus-compute-endpoint <your-application>
```

Create the endpoint:

```
globus-compute-endpoint configure my-endpoint
```

Replace the generated template with the closest one from `systems/endpoints/`, editing
the marked lines — account, filesystems, and the `worker_init` that activates your
environment:

```
~/.globus_compute/my-endpoint/user_config_template.yaml.j2
```

Start it and note the UUID. The first start may open a browser-based Globus login,
which only you can complete:

```
globus-compute-endpoint start my-endpoint && globus-compute-endpoint list
```

## 3. Describe the system

`systems/<system>.json` holds what is true for everyone on that machine — module line,
proxy, cache paths, queue defaults. One file per machine, shared by every campaign.

## 4. Describe your access

`users/<you>/<system>.json` holds what is yours: the endpoint UUID, the account to
charge, and a writable working directory on the compute system.

```json
{
  "endpoint": "<uuid from step 2>",
  "account": "<project>",
  "work_dir": "/path/on/compute/system/agentlab_runs"
}
```

Create that directory on the compute system before the first run.

## 5. Run

```
cd campaigns/<name> && ./run.sh
```

Each campaign's `run.sh` holds its own settings — job budget, wallclock cap, Slack.

Start it inside tmux. A campaign runs for hours or days, and closing the terminal or
dropping an ssh session kills it with jobs in flight and their results uncollected.

```
tmux new -s agentlab
```

```
framework/list_agents.sh --all          every run, and its outcome
framework/kill_agent.sh --drain <run>   stop cleanly, finishing jobs in flight
```

Preflight checks the task contract, the endpoint status and workspace writability
before the agent starts, so most misconfigurations fail immediately with a message
rather than mid-run.

## Failure signatures

**`ENDPOINT_NOT_ONLINE` on the first submission.** The endpoint manager forks a user
endpoint process on demand, and the first submit can arrive before it signals
readiness. Resubmit; it is a timing effect, not a configuration error.

**`ManagerLost`.** The batch allocation reached its walltime while your task was
running. Blocks persist between jobs, so a long-lived block eventually expires and
takes the in-flight task with it. A job lost this way carries no information about what
it was testing — resubmit it. Raise the walltime in `systems/<system>.json` if it
happens often.

**Submits succeed and nothing comes back, with no error.** The endpoint is online and
accepting tasks, but its workers never start — usually a `worker_init` that fails, or a
queue request the scheduler will never satisfy. Offline endpoints and expired
allocations both report themselves, so silence points here. Check the endpoint's log on
the compute system, and whether a batch job was ever queued.
