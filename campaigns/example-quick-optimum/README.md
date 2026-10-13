# Basic example: the optimum of a noisy response

One agent tunes a single dial, `setting` (0 to 10), to find where a noisy response is
lowest, and states it with an interval its readings support. Each reading runs a short
Metropolis sampler, about a second of CPU, and the agent chooses where to measure and
how many readings to average. A run takes minutes.

## Run

```
./run.sh
```

Runs on the local machine. Watch it with `bin/watch.sh`; results go to
`workspace/example-quick-optimum/`, with the agent's conclusions in `LOGBOOK.md`.

## Variants

| script | what changes |
|---|---|
| `run.sh` | the jobs run on the local machine |
| `run_remote.sh` | the jobs run on a remote system through Globus Compute (`SYSTEM`, default `gce`); its endpoint and work directory are in `users/<you>/<system>.json` |
| `run_with_critic.sh` | a critic model reviews each cycle |
| `run_nemotron.sh` | the agent runs on Nemotron Ultra through the ALCF inference service (`run_nemotron.settings.json`) |

## Files

| file | what it holds |
|---|---|
| `prompt.md` | the goal, what is fixed, what may vary, leads |
| `user_prompt.md`, `user_prompt_remote.md` | what this run is for |
| `task.py` | one job: `local_fn` for the local machine, `remote_fn` for a remote system |
| `campaign.json` | the system and how many jobs run at once |
| `method.md` | how the agent works, copied from `methods/` |
