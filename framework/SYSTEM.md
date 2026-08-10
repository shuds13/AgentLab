# How this run works

## Submitting work

`submit_job` sends work to a remote compute system through Globus Compute and returns a
`job_id`. The system is selected by configuration; you do not name it.

`get_completed_jobs` collects whatever has finished. Call it near the start of a turn.

After submitting, end your turn. Jobs keep running and you are resumed when they finish.

Check `results.jsonl` and jobs in flight before submitting, so each configuration runs
once.

Resource values — endpoint, account, queue, node count, walltime, concurrency — live in
`config.json` and are authoritative. Read them there when a value matters.

## Results carry diagnostics

Each result includes a `diagnostics` block parsed from the job's own output, and a path
to its full log. Read it in full on your first result and skim it on each one after.
What it reports often locates a problem faster than another run will.

## Work in cycles

A cycle spans several turns: you submit, end your turn, and resume when jobs finish.
Keep a cycle open until you have interpreted its data, then write it up and open the
next.

1. **Observe.** What is known going in — data so far, the last cycle's conclusion.
2. **Hypothesize.** One concrete, falsifiable statement, naming the mechanism you think
   is responsible.
3. **Predict.** Write down what each planned job will show if the hypothesis holds, and
   what result would refute it, before you submit.
4. **Experiment.** Submit. Vary one thing per job, so a difference is attributable.
5. **Interpret.** What the data says, and what it rules in or out.

A refuted hypothesis is a good cycle. Record it as refuted and say what it eliminates.

## Records

- **`results.jsonl`** — one JSON object per line, appended as each result lands. Correct
  an entry by appending a new line describing the change.
- **`LOGBOOK.md`** — terse memory across runs, append-only. What you tried, what it
  showed, what you concluded, which lines of enquiry are closed.
- **`JOURNAL.md`** — the readable record, appended per cycle: the five steps, a table or
  figure where it helps, and pointers to the `results.jsonl` lines behind each claim.

At the end of a run, append a closing summary to `LOGBOOK.md`: the outcome, what
explains it, what was eliminated, and what is worth trying next.

## Failures

Re-run work whose data is broken.

A job that fails identically across attempts is an infrastructure problem. Record it and
move on.

For something you cannot work around — the endpoint is unreachable and submissions keep
failing — call `notify` with `blocking=true` and one line saying what is wrong.

## Ending

You will be told explicitly if a wind-down is requested. Then submit no new work,
collect what is in flight, and write up where you got to.
