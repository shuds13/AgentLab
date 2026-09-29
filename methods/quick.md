# How this run works

A short run, meant to be read. Say what you found in a few lines — someone should be
able to open `LOGBOOK.md` and see what happened without reading a report.

## Work in cycles

A cycle is: decide what to find out, submit the jobs that would settle it, read what
came back.

1. **Ask.** One thing you want to know, and what result would answer it.
2. **Submit.** All the jobs that bear on it, in one go.
3. **Read.** What the results say, and whether they answered it.
4. **Close.** Add the cycle to `LOGBOOK.md`, then call `cycle_done`, before opening the
   next one.
   Then ask whether this run's goal is met. If it is, call `goal_met` with what
   settles it instead of opening another cycle.

An answer of no is a finished cycle.

## Records

- **`results.jsonl`** — one JSON object per line, appended as each result lands. Every
  number you rely on lives here; a number that exists only in prose cannot be checked.
- **`LOGBOOK.md`** — a few lines per cycle: what you asked, what came back, what you
  concluded. You start each run with no memory of the last one, so what is not written
  here is lost.

Keep it short. A cycle entry is a handful of lines, not a section.

## The write-up

`JOURNAL.md` is what someone reads to find out what the run established. Append a
section as each cycle closes, so it is current rather than written from memory at the
end.

A cycle section is a heading, a table of what was run, and at most three bullets. No
paragraphs:

    ### Cycle 3 - <what this cycle varied>

    | <setting> | <metric> | <cost> |
    |---|---|---|
    | A | 4 | 92 |
    | B | 4 | 225 |

    - A reaches the same result for a fraction of B's cost.
    - B is worst or equal on every run.
    - A against the others: not separable from what we have.

One bullet, one point, one line. Put numbers in the table, not in the bullets. Where a
comparison is not settled, say so in a bullet and stop there.

Make a figure where it helps. Plot with matplotlib through `Bash`, save it under
`figures/` and reference it from the journal with a caption saying what it shows.

Scripts you write to fit, check or plot go in `scratch/`, with anything they produce
that is not a record. The top of the workspace holds the records and nothing else, so
that what a later reader finds there is what the run concluded.

## Ending

Add a closing entry to `LOGBOOK.md`: what you found, and what you would do next with
more jobs.

Then end `JOURNAL.md` with an `## Executive Summary` of at most six bullets, for
someone who reads only this:

    ## Executive Summary
    - The answer, in one sentence.
    - The numbers it rests on.
    - What is not settled, and why.
    - What you would do next.

One line each. Say what is unsettled as plainly as what is.
