# Workflow: working the review queue

Objective: turn a queued recommendation into a decision - approve the buffer,
edit it, or reject it - and, once approved, actually publish it.

Nothing reaches your channel manager without going through this. `mode:
shadow` blocks the publish step for every item, approved or not; see
`docs/safety.md` for the full guard.

## Steps

1. **See what is waiting.**
   ```bash
   make review
   ```
   Each line shows the item id, its status (`pending_review` or
   `needs_human`), the date, and the recommended buffer.

2. **Read one in full.**
   ```bash
   python3 tools/review.py show <id>
   ```
   This prints every scored arrival (with the `basis` for its risk score),
   the sum-and-floor arithmetic, the recommendation, and - if the night is
   sold out - the full walk plan: the ladder, the reasons, the cost card.
   Summarise it for the duty manager in plain language: how many rooms over,
   who moves first and why, what it costs if it happens, what the property
   protects if it does not.

3. **Decide.**
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py edit <id> --buffer 1 --note "playing it safer tonight"
   python3 tools/review.py reject <id> --reason "board meeting in house, no appetite for risk"
   ```
   `edit` overrides the recommended number and **re-costs the whole walk
   plan** against it (partner rate, cost per guest, coverage ratio,
   Poisson-binomial risk arithmetic) - what gets published always matches
   what the duty manager actually decided, never the original recommendation.
   The before/after buffer is recorded as a `learnings` row for anyone
   reviewing the history later.

4. **Publish what was approved.** This is the equivalent of the demo's "Set
   controlled oversell" button - it is what actually raises the sell limit.
   ```bash
   python3 tools/review.py publish
   ```
   This claims everything `approved`/`edited` and writes one row to
   `data/exports/oversell_log.csv` (or your Google Sheet - see
   `docs/integrations.md`). **In `mode: shadow` nothing is written at all,
   approved or not**: the guard blocks it with a readable message
   (`blocked ... (approval kept)`), the item returns to `approved`, and it
   will only actually publish after you flip `mode: live` (clear the old
   queue first with `python3 tools/review.py stale` - see
   `workflows/90-go-live.md`). A buffer of 0 (a human approved "no oversell")
   has nothing to publish and is simply marked done.

5. **A failed publish.** `publish` marks the item `failed` with the error
   attached.
   ```bash
   python3 tools/review.py retry <id>
   ```
   re-queues it for another attempt after you have fixed the cause (usually
   a Sheets/CSV permissions issue - `make doctor` will say which).

6. **Get the plan into the right hands.** Publishing writes the audit row;
   it does not phone anyone. Read `knowledge/walk-partner-protocol.md` and
   tell the duty manager on shift, or forward `python3 tools/review.py show
   <id>`'s output - see `docs/integrations.md` for why this template does
   not automate that last step.

## Rules

- Only `tools/review.py` writes `approved` / `edited` / `rejected`.
- The agent never decides who actually gets walked at 22:00 - that is
  always the duty manager, using the ladder as a starting point, not an
  instruction. See `docs/safety.md`.
- Confirm with the hotel before publishing anything, even an approved item,
  the first few times. `workflows/90-go-live.md` covers when to stop doing
  that.
