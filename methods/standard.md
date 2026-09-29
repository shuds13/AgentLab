# How this run works

## Work in cycles

A cycle spans several turns: you submit, end your turn, and resume when jobs finish.
Keep a cycle open until you have interpreted its data, then write it up and open the
next.

1. **Hypothesize.** One concrete, falsifiable statement, naming the mechanism you think
   is responsible, what the planned jobs will show if it holds, and what result would
   refute it — written before you submit.
2. **Experiment.** Submit. Vary one thing per job, so a difference is attributable.
3. **Interpret.** What the data says.
4. **Close.** Append the cycle's `JOURNAL.md` entry, then call `cycle_done` with what
   it established, before opening the next one.
   Then ask whether this run's goal is met. If it is, call `goal_met` with what
   settles it instead of opening another cycle.

A refuted hypothesis is a good cycle.

## Records

Three files, all in the shared area.

- **`results.jsonl`** — one JSON object per line, appended as each result lands. Correct
  an entry by appending a new line describing the change.
- **`JOURNAL.md`** — what happened, for someone who was not here. One section per cycle,
  appended as that cycle closes. A heading, a table of what was run, and at most three
  bullets. No paragraphs: name the thing you ran, the setting you changed and the number
  that came back, and put the numbers in the table rather than in the bullets. Where a
  comparison is not settled, say so in a bullet and stop there.
- **`LOGBOOK.md`** — your memory across runs, append-only, terse. What you tried, what it
  showed, what you concluded, which lines of enquiry are closed. You start each run with
  no memory of the last one, so anything not written here is lost — including the things
  a result row cannot hold: a setting the tool refused, a region not worth revisiting, a
  number that turned out to be noise.

Keep the entries short. Numbers live in `results.jsonl`; the logbook holds what you
decided and why.

## Figures

Only make a figure when it is genuinely helpful. Plot with matplotlib through
`Bash` and save under `figures/` in the shared area, referenced from the journal entry
with a caption saying what it shows.

## Ending

Append a closing summary to `LOGBOOK.md`: the outcome, what explains it, what was
eliminated, and what is worth trying next.
