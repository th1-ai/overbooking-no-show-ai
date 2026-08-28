# Workflow: the nightly scan

Objective: score tonight's (and, if configured, the next few nights')
arrivals for no-show risk, recommend a controlled oversell buffer, and
prepare the walk plan if the night is sold out - so the duty manager has a
decision ready, not a blank page, before the evening rush.

Inputs: your PMS's arrivals and rates (or the bundled fixtures, if you have
not connected one yet). No LLM call anywhere in this workflow - see
`docs/how-it-works.md` "Design decisions" #0.

## Steps

1. **Check the agent is healthy.**
   ```bash
   make doctor
   ```
   Any `FAIL` line has a fix hint. Fix it before going further.

2. **Run one scan.**
   ```bash
   make run                                    # scans horizon_nights, starting tonight
   make run ARGS="--date 2026-09-15"           # a specific night
   make run ARGS="--nights 7"                  # a week at once
   make run ARGS="--dry-run"                   # compute and print, write nothing
   ```
   Read the last line: `N items processed, N drafted, 0 sent (shadow)`. Every
   scan is `0 sent` - a scan only ever queues a recommendation, it never
   publishes anything (see `workflows/80-review.md`).

3. **See what came out of it.**
   ```bash
   make review
   ```
   A night lands in one of three places:
   - **Nothing to review** - the night was not sold out, or the expected
     no-shows did not add up to a whole room. Logged as `skipped`, and that
     is a success worth reporting, not a gap. `python3 tools/review.py list
     --status skipped` shows the recent ones.
   - **`pending_review`** - sold out, a buffer is recommended, a walk plan
     is ready. This is what needs a human. Go to `workflows/80-review.md`.
   - **`needs_human`** - something is missing (capacity not configured, or
     no rate on file for `reference_room_type`) and the agent could not
     finish the recommendation. `make doctor` names the gap.

4. **Read one recommendation in full.**
   ```bash
   python3 tools/review.py show <id>
   ```
   Explain it to the person running the desk in plain language: how many
   arrivals, how many carry real risk and why (the `basis` line per guest),
   the sum-and-floor arithmetic, the recommended buffer, and - if there is a
   walk plan - who is first on the ladder, why them, and what the cost card
   says (partner rate, taxi, goodwill, the real coverage ratio - never an
   asserted "it always pays for itself").

5. **Hand it to `workflows/80-review.md`.** That is where a human approves,
   edits the buffer, or rejects the recommendation, and where the publish
   step actually happens (never automatically, never in shadow mode).

## What runs when

`config/agent.yaml: schedule:` lists this job as `scan`, by default `0 18 * *
*` (18:00 daily, before the evening's no-show pattern is settled but with
enough runway for a duty manager to review it). `make schedule ARGS="--all"`
prints the exact cron/launchd/systemd snippet - see README §9.

The morning-after read-back is a separate workflow -
`workflows/11-reconcile.md`.
