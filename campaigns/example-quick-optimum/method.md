# How this run works

A short run, meant to be read. Say what you found in a few lines — someone should be
able to open `LOGBOOK.md` and see what happened without reading a report.

## Work in cycles

A cycle is: decide what to find out, submit the jobs that would settle it, read what
came back.

1. **Ask.** One thing you want to know, and what result would answer it.
2. **Submit.** {jobs_at_once} jobs, the number that run at once: the settings that
   answer the question, and where they do not fill {jobs_at_once}, the settings worth
   knowing next. Then end your turn; the runner wakes you when the results are in.
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

`JOURNAL.md` is what someone reads when the run is over: a section per cycle, in order,
saying what you asked, what came back and what you concluded. Plot the run's readings:
make the figures with matplotlib through `Bash`, save them under `figures/` and
reference them from the journal with a caption saying what each shows. Keep the section
to a few paragraphs; `LOGBOOK.md` holds the running notes and `results.jsonl` holds the
numbers.

Scripts you write to fit, check or plot go in `scratch/`, with anything they produce
that is not a record. The top of the workspace holds the records and nothing else, so
that what a later reader finds there is what the run concluded.

## Ending

Add a closing entry: what you found, and what you would do next with more jobs.
