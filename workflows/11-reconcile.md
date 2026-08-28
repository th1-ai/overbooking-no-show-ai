# Workflow: the morning-after reconcile

Objective: read back what actually happened overnight and write one honest
note - did the buffer pay off, did rooms stand empty, did anyone still have
to be walked - so there is a record beyond memory, and so `make report` has
real numbers to show.

This workflow only reads. It never writes to your PMS, never contacts a
guest, and never changes a room's status - that is your property's own night
audit process (see `docs/how-it-works.md` "Design decisions" #7). If a
recommendation was never approved and published, this step still runs and
still tells you the truth: what would have happened if it had been.

## Steps

1. **Run the reconcile pass**, normally the morning after a scan.
   ```bash
   make run ARGS="--reconcile"                          # yesterday, by default
   make run ARGS="--reconcile --date 2026-09-15"         # a specific night
   make run ARGS="--reconcile --date 2026-09-10 --nights 7"   # a run of nights
   ```
2. **Read the note.** One of four shapes, always with names and euros (or
   your own currency) in it, never a euphemism:
   - Nobody no-showed and a buffer was published -> the oversell had to be
     honoured elsewhere.
   - Nobody no-showed and the buffer was 0 -> "buffer 0 was the right call."
   - No-shows happened and nothing was published -> the rooms stood empty,
     priced.
   - No-shows happened and a buffer was published -> how many were absorbed,
     what was recovered, and whether anyone still had to be walked.
3. **Nothing to reconcile is a normal result.** A night that was `skipped`
   (no oversell recommended) has nothing to read back - the pass says so and
   moves on.
4. **Check the numbers over time.**
   ```bash
   make report
   ```
   Shows nights scanned, sold-out nights, rooms recommended vs rooms
   actually published, and the most recent reconcile notes. See
   `docs/benefits.md` for what to do with this.

## What runs when

`config/agent.yaml: schedule:` lists this job as `reconcile`, by default `0 9
* * *` (09:00 daily - after the property's own night audit has closed out
each arrival's real status). `make schedule ARGS="--all"` prints the exact
snippet.
