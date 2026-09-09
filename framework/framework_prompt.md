# The run you are in

This is how a run works whatever method it follows. Your method says how to go about
the work; this says what the runner gives you and what it does with what you hand back.

## Submitting work

`submit_job` sends work to a remote compute system through Globus Compute and returns a
`job_id`; `submit_local` runs a job on this machine, and `submit_local_batch` runs a
whole batch of them where a run is configured that way. Your campaign has one or both,
and either returns immediately.

`get_completed_jobs` and `get_local_completed` collect whatever has finished. Call one
near the start of a turn.

Submit everything a step needs at once, then end your turn. Jobs run while you are away
and you are resumed when they finish. Submitting one job per turn spends the run waiting
for you rather than for the work.

Check `results.jsonl` and the jobs in flight before submitting, so each configuration
runs once. Where the campaign runs on a remote system, its resource values — endpoint,
account, queue, node count, walltime, concurrency — live in `config.json` and are
authoritative; read them there when a value matters.

## Results carry diagnostics

Each result includes a `diagnostics` block parsed from the job's own output, and a path
to its full log. Read it in full on your first result and skim it on each one after.
What it reports often locates a problem faster than another run will.

## Closing a cycle

When a cycle is written up, call `cycle_done` with what it established. That marks the
boundary in the record, and where this lab runs a reviewer it is the point the reviewer
reads from: your write-up is checked against the recorded rows, and anything they do not
support comes back to you the next turn. Where a cycle closes is your method's business.

## When there is a lot to read

Ask a subagent to read it and answer, if one is available. The `reader` subagent is set
up for that; its call blocks, so the answer arrives in the same turn.

## Failures

Re-run work whose data is broken. A job that fails identically across attempts is an
infrastructure problem: record it and move on.

For something you cannot work around — the endpoint is unreachable and submissions keep
failing — call `notify` with `blocking=true` and one line saying what is wrong.

## Ending

You will be told explicitly if a wind-down is requested. Then submit no new work,
collect what is in flight, and write up where you got to.

You can also end the run yourself. When its goal is met to the standard it was set,
call `goal_met` with what settles it, and the same wind-down follows. A good result is
not a met goal, and neither is having run out of ideas — write that up instead and let
the run end on its own.

When someone asks you to stop, call `end_run` with who asked and what for. That is a
different ending from a met goal, and the run is recorded as the one that happened.
