# Finding the optimum of a noisy response

One dial, `setting`, runs from 0 to 10. The system's `response` to it has a single
lowest point. Find that point, say how precisely you know it, and account for the shape
of the curve either side.

Every reading carries noise. Two settings whose means differ by less than the noise are
not told apart by one reading each — the difference you see is as likely to be the noise
as the system. A job can take several readings at one setting and average them, which
tightens the estimate, and it reports the spread of those readings alongside the mean.

The run produces two things: where the optimum is, with an interval you can defend, and
what the shape of the response says about the system.

## Fixed

The response itself. Nothing you do changes the underlying system, so a difference
between two jobs is either the setting or the noise.

## Variable

`setting`, and how many `replicates` to spend on it.

## Leads, roughly by expected payoff

These are hypotheses. Confirm one against your own results before concluding from it.

1. **Bracket before you refine.** Settings spread across the range tell you which part
   of it holds the optimum; refining inside a bracket you have not established wastes
   jobs on the wrong region.
2. **Spend replicates where the decision is close.** Far from the optimum the
   differences are large and one reading settles them. Near it they are within the
   noise, and that is where averaging earns its cost.
3. **The curve need not be symmetric.** Whether it rises at the same rate either side
   is measurable, and it changes where the optimum sits relative to the readings you
   have.

## What counts as knowing the answer

An optimum stated to more precision than the readings support is not a finding. Say
what the data bounds, and what it would take to bound it more tightly. A claim about the
shape of the curve — that it is quadratic, that a term dominates above some point —
needs readings that separate that shape from the alternatives, not a plausible story
about the numbers you happen to have.

## Budget

Jobs are cheap here — about a second each — so the constraint is not machine time but
how many readings a conclusion honestly needs.
