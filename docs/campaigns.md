# Writing and maintaining a campaign

Guidance, not rules. It describes what has worked and what tends to go wrong; the person
whose campaign it is decides. If they want a specific result, a date or a version in
`prompt.md`, put it there — mention the cost once if it is worth mentioning, and follow
what they ask.

Two situations, and they differ:

- **Creating** a campaign — writing the files for the first time. `AGENTS.md` covers the
  steps; the sections below say what belongs in each file.
- **Editing** an existing one — setting up a later run, or changing something. Here the
  files already reflect decisions someone made. Change what the person asks for and leave
  the rest, even where it differs from what is below. A campaign that has been run is a
  record as much as a configuration.

## What a campaign is

A standing piece of work: a goal, an instrument, and a record that outlives any one run.
Some campaigns run once. Many are run again — to extend a result, to follow a lead the
last run left open, or because something the work depends on has changed.

Where a campaign will be run again, the aim is that a later run needs no edit to anything
but `user_prompt.md`. The test for anything you write: **will this still be true next
time it runs?** If not, it belongs in `user_prompt.md` or the workspace.

## Length

Campaign files are read on every run, so what is in them costs something each time. In
what you write, say a thing once, in as few words as carry it.

Text the person gave you — a prompt, a specification, a procedure — goes in as it stands.

`user_prompt.md` matters most, often being rewritten each run: keep it as small as expresses
what the run is for.

## What goes where

| file | changes | holds |
|---|---|---|
| `prompt.md` | rarely | the campaign's standing objective, and what holds across every run |
| `method.md` | rarely | how the agent works — cycles, records |
| `campaign.json` | when the system or resources change | which system, and the parameters a job needs |
| `task.py` | when the instrument changes | what one job runs and returns |
| `user_prompt.md` | every run | this run's aim toward that objective, and any note for this run |
| workspace files | written each run | the agent's records |

If a rerun needs an edit outside `user_prompt.md`, that is usually a sign something
durable was written in the wrong place.

## prompt.md

Standing, and reread every run as part of the system prompt.

It holds the campaign's objective — what the campaign exists to find out, unchanged from
one run to the next — along with anything else true of every run: what is held fixed so
results stay comparable, and how the agent should work.

Where the person gave you a prompt, it goes in as they wrote it. What the campaign needs
beyond it is added alongside, so which is which stays clear.

What you established yourself — how a job runs, what a measurement reproduced, where the
code and the prior results are — goes in a file of its own that this one names in a line.

A campaign has the sections its own goal needs.

`user_prompt.md` holds this run's aim: which part of that objective this run goes after,
and when it stops. The objective outlives the run; the aim does not.

Things that tend to cause trouble here:

- **Results.** Any value the campaign measures is superseded by the next run, so the file
  needs editing to stay right. Saying what is measured, and where the numbers are kept,
  does not.
- **Dates and version numbers.** A reference the campaign is measured against is usually
  replaced once a run beats it.
- **This run's aim**, which belongs in `user_prompt.md`, and **findings**, which belong in
  the logbook.

Grepping the file for a four-digit year or a version string is a quick way to spot these.

When editing, this is the file to leave alone unless the change is genuinely standing.

## user_prompt.md

Often updated before a run; it is what starts the agent. Keep it short — this is the file the
person editing the campaign touches every time.

- What this run is for, and what would answer it.
- Anything that changed since the last run.
- When to stop — for each way the run could go, not just the expected one.

This is the file a later run is expected to change, so it is where an edit goes by
default.

## method.md

**Creating:** copy one from `methods/` into the campaign, so each campaign owns its own
and can be changed without affecting another. `AGENTS.md` says which to pick.

**Editing:** the campaign's copy may have been changed on purpose. Do not overwrite it
from `methods/`. Compare the two only when something suggests the copy has gone stale —
it names a tool that no longer exists, or describes records the framework no longer keeps
— and then change the parts that are wrong rather than replacing the file.

## The workspace

Written by the agent, not by you. What is in it is whatever the campaign asks for —
usually the records named in `method.md`, or in `prompt.md` where the campaign has no
`method.md`.

A logbook is dated entries under a heading per run, appended.

A separate state or handover file tends to go wrong: it duplicates the logbook and the
two drift apart. Current state is the newest logbook entry; the next run's aim is
`user_prompt.md`. Where one is genuinely wanted, reference it from `user_prompt.md`, and
give it a name that will not mislead a later run — anything like "next-run" describes a
moment, and is wrong from the following run onward.

Where a campaign produces one comparable set of numbers per run, a small table with a row
appended each run is easier to read than the logbook or the raw records.

## Setting up a later run

1. Read the last logbook entry — what the previous run concluded and left open.
2. Update `user_prompt.md` for this run, as needed.
3. Change `campaign.json` if the system or resources differ.
