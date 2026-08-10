# Improving Llama-3-70B inference on Aurora

Find a configuration that lowers per-token decode rate for Llama-3-70B on a single
Aurora node, and explain what sets the limit.

Decode rate is the latency difference between two output lengths at the same input
length:

```
ms_per_token = (latency_at_out2048 - latency_at_out128) / 1920 * 1000
```

Measuring it this way cancels prefill and startup, so the number reflects generation
alone. Establish the current figure first — it is the reference every later result is
compared against.

The run produces two things: a faster configuration, and an account of what limits
performance.

## Fixed

Model, one node, and the input/output shapes used for comparison. These keep results
comparable across jobs.

## Variable

`tensor_parallel_size`, `dtype`, `enforce_eager`, `max_num_seqs`, `max_model_len`, and
anything in `env_extra`, which sets environment variables before the benchmark starts —
so device hierarchy, affinity and communication-library settings need no code change.

## Leads, roughly by expected payoff

These are hypotheses, and the Intel-specific variable names come from general knowledge
rather than from this system. Confirm a variable exists before drawing a conclusion from
a negative result.

1. **Graph capture.** Read `diagnostics.graph_capture` on the first result. If the
   platform forces eager execution, decode is where that costs most. Establish whether
   capture can be enabled at all on this backend and version — through configuration,
   environment, or a different build — before spending jobs on it.
2. **vLLM version.** `diagnostics.vllm_version` reports what the endpoint provides.
   Intel GPU support moves quickly, so check what the installed version does and does
   not implement.
3. **Parallel group layout.** Aurora has 12 tiles across 6 GPUs. A group spanning both
   intra-GPU and inter-GPU links may behave differently from one that does not. Decode
   performs an all-reduce per layer per token, the path most sensitive to group shape.
   Attention head count must divide evenly by the tensor-parallel size.
4. **Device hierarchy.** `ZE_FLAT_DEVICE_HIERARCHY` (FLAT or COMPOSITE) determines
   whether the runtime presents tiles or whole GPUs, which changes what a given
   tensor-parallel size means.
5. **dtype.** Compare bf16 and fp16 directly.
6. **Affinity and communication.** Worker count, affinity masks, and the communication
   library's transport and algorithm settings.
7. **Vendor guidance.** The facility documents a recommended environment for this
   application. Read it and test anything in it that the current configuration omits.

## Budget

One node on the debug queue, one benchmark at a time. Collect an outstanding result
before submitting work whose choice depends on it.

## When to stop

Stop and write your conclusion when either holds:

- **You have a tested explanation.** You can say what limits performance, a prediction
  drawn from it held, and you can state what would have refuted it. Confirm it once,
  then stop.
- **The leads are exhausted.** Everything above has been tried. Report the best
  configuration found, what was eliminated and the evidence that eliminated it, and what
  further access or information would let someone go further.

Finding a faster configuration you understand ends the run. Finding one you cannot
explain means investigating the reason before going on.

## Deliverable: SKILL.md

Alongside the journal, write `SKILL.md` in the workspace. It is guidance for someone
tuning single-node inference on a machine and model that are not these ones.

Draft it once you have a first confirmed finding and revise it as more land, so it
exists even if the run ends early.

Include:

- **How to measure.** The procedure that isolates the quantity you were optimising,
  precise enough to repeat elsewhere.
- **Which settings mattered, in order**, each with the measurement that shows it.
- **Best configuration found**, its number, the model, the machine, and the date.
- **What to check in the startup diagnostics first**, and what specific lines indicate.
- **What was ruled out**, and the evidence that ruled it out.

Every claim comes from a run you did. Leave out the leads listed above unless your own
results established them — a reader cannot tell a measured result from a guess, and will
act on both.

Say which parts are specific to this machine and model, and which you expect to carry
over.
