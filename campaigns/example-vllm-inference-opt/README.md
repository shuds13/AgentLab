# A campaign directory

This one is an example. Copy it and replace the contents with your own problem.

## Required

| file | what it holds |
|---|---|
| `prompt.md` | the goal, what is fixed, what may vary, what counts as an answer, when to stop |
| `user_prompt.md` | what to do first |
| `task.py` | how one job runs, what it returns, and what the agent is told about it |
| `campaign.json` | which system, and any parameters for it |

## Anything else your problem needs

The directory is yours. Whatever you put here, the agent can read:

- baseline measurements to compare against
- input data, configuration files, model lists
- reference documents, vendor guidance, prior results
- scripts the job runs, if `task.py` invokes them rather than building a command

Point at them from `prompt.md` by relative path, and say what they are for.

Output does not go here. Everything the agent produces is written to
`workspace/<campaign>/`.

## This example

Tunes single-node vLLM inference: it measures per-token decode rate for a model on one
node and searches for a configuration that lowers it. `task.py` runs one benchmark
configuration and returns the metrics together with the vLLM startup diagnostics.

It ships without baseline data. A real campaign of this shape would include the
measurements it is trying to beat.
