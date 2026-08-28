---
name: overbooking-no-show-ai
description: Run Overbooking & No-Show AI ("The Juggler") — Predicts no-shows and late cancellations per night from booking history and lead-time patterns, recommends a safe controlled-overbooking level, and preps the walk plan (which booking, which partner hotel) if the night runs over.. Use when the user asks to run the agent, check what is waiting for review, approve or reject a draft, or asks how the agent is doing. Trigger phrases: "run The Juggler", "/overbooking-no-show-ai", "check the queue", "what is waiting for me", "approve that draft".
---

# Overbooking & No-Show AI

Runs Overbooking & No-Show AI and works its review queue. Everything happens
from the repo root; every command below exists and works. This agent has no
LLM step at all - every number is a formula over PMS data, so there is no
`interactive` pending prompt to answer here (unlike most other repos in this
family).

## Before anything else

Read `README.md` if you have not this session, and `workflows/10-scan.md` and
`workflows/11-reconcile.md` for the main loop. If the user has never run this
agent, start at `workflows/00-setup.md` instead and walk them through it.

## The loop

**1. Check the agent is healthy.**

```bash
make doctor
```

Any `FAIL` line has a fix hint. Fix it before going further. `WARN` lines are
worth mentioning but do not stop the run.

**2. Scan tonight (and, if configured, the next few nights).**

```bash
make run                          # tonight, per horizon_nights
make run ARGS="--date 2026-09-15" # a specific night
make run ARGS="--nights 7"        # a week at once
make run ARGS="--dry-run"         # compute and print, write nothing
```

**3. Show what is waiting.**

```bash
make review
python3 tools/review.py show <id>
```

Summarise it for the user in plain language: how many arrivals, how many
carry real no-show risk and why, the recommended buffer, and - if the night
is sold out - who is first on the walk ladder and what it would cost. Do not
paste raw JSON at them.

**4. Act on their decision.**

```bash
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --buffer 1 --note "<why>"
python3 tools/review.py reject <id> --reason "<why>"
```

There is no free-text draft here - `edit` overrides the recommended buffer
with a number and re-costs the whole walk plan against it, so what gets
published matches what the duty manager actually decided.

**5. Publish what was approved.**

```bash
python3 tools/review.py publish
```

Writes one audit row per published buffer (`data/exports/oversell_log.csv`).
In shadow mode this is always blocked - "approval kept" - and that is
correct, not a bug.

**6. The morning after, read back what happened.**

```bash
python3 tools/run.py --once --reconcile
```

Read-only. Never write to the PMS, never contact a guest - see
`docs/safety.md`.

**7. Report.**

```bash
make report
```

## Rules

- **Never publish in shadow mode**, and never work around a blocked write.
  The error message says what to do.
- **Going live is the hotel's decision.** Only raise it after
  `workflows/90-go-live.md` has been worked through.
- **This agent never walks a guest and never contacts one, in shadow or in
  live mode.** That is structural, not a setting - the walk itself is always
  a human decision at the desk. Never suggest otherwise.
- **Never print or paste a credential.**
- If a run fails, read the whole error, fix the cause, re-run, and note what
  you learned in `workflows/99-troubleshooting.md`.
