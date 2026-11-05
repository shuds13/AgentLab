# The shape with the least drag

Air flows through a wind tunnel past a single shape. Find the shape that the air drags
on least, for a fixed amount of material: every shape has the same area, that of a
circle of diameter D. Say how much less drag it has than a circle, and what about its
form makes the difference.

The tunnel is a simulation on a GPU, run at a Reynolds number of 100, slow enough that
the flow is smooth close to the shape, fast enough that a blunt shape sheds a trail of
swirls (vortices) behind it. Drag comes from two things: the pressure difference
between the shape's front and its back, which a large wake makes worse, and friction
along its surface, which a long shape makes worse. The best shape balances the two.

Each job returns a picture of the flow. Look at it: where the air leaves the surface,
how wide the wake is and whether it sheds vortices say more about why a shape does well
than the drag number alone.

## Fixed

The tunnel, the flow speed, the Reynolds number, and the shape's area.

## Variable

The outline (`radii`) and the resolution the shape is simulated at.

## Leads, roughly by expected payoff

These are hypotheses. Confirm one against your own results before concluding from it.

1. **Compare at a coarse resolution, confirm at a fine one.** Resolution 20 or 40 takes
   seconds and ranks shapes well enough to find a good region; the drag of the shapes
   you conclude from should be measured again at 80 or 160, since coarse runs are off by
   several per cent.
2. **The back matters more than the front.** A blunt back leaves a wide, swirling wake.
3. **Too long costs friction.** A needle has little wake but a lot of surface.

## What counts as knowing the answer

A drag difference smaller than the change it shows between resolutions is not a
finding. State the best shape's drag at the finest resolution you ran, beside the
circle's at the same resolution, and show the picture of each in `JOURNAL.md` as
`![caption](outputs/...)`, its path relative to the workspace.

## Budget

A job is one GPU for seconds to a few minutes; four run at once on a node. The time
that matters is the queue.
