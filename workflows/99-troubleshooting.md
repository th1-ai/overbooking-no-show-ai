# Workflow: troubleshooting

Read the whole error before doing anything - every tool here is written to
say what broke and what to do about it. If you fix something not covered
below, add it here.

## `make doctor` shows a FAIL

Each `FAIL` line has a `->` fix hint right under it. Common ones:

- **`hotel identity`: name is still 'Hotel Aurora'.** Expected on a fresh
  clone. Edit `config/hotel.yaml`.
- **`capacity`: hotel.rooms is 0 or not set.** Set `hotel.rooms` in
  `config/hotel.yaml` to your real room count - without it the agent cannot
  tell whether a night is sold out.
- **`reference room type`: reference_room_type is not set.** Set
  `config/agent.yaml: reference_room_type` to a real room type id your PMS
  uses.
- **`walk partners`: walk.partners is empty.** List at least one partner
  hotel in `config/agent.yaml: walk.partners`.
- **An adapter shows FAIL, not warn.** `universal`/`built` adapters fail loud
  when misconfigured (a `warn` is reserved for stubs). Read the `detail`
  column - it names the missing file or variable.

## `make demo` does not print `DEMO OK`

- Make sure `make setup` ran first (`.venv` must exist).
- `tools/demo.py` forces `mode: shadow` and the `mock` PMS adapter and reads
  `fixtures/hotel/*.json` - if you deleted or renamed those files, restore
  them from git.
- Read the traceback if there is one; `tools/demo.py` does not swallow
  errors on purpose, so a fixture problem shows up immediately.

## A night that should be sold out is not flagged

`sold_out` is `capacity > 0 and otb_rooms >= capacity`, where `capacity` is
`hotel.rooms` and `otb_rooms` is every reservation in house that night that
has not cancelled or no-showed. If your PMS is closing out rooms for
maintenance or holding some back, `hotel.rooms` may need to reflect
sellable rooms, not the physical room count - see
`docs/how-it-works.md` "The oversell recommendation".

## No walk plan even though the night is sold out

`build_walk_plan()` returns nothing when both the published buffer and the
recommended buffer are 0 - there is nothing to plan for. Check
`python3 tools/review.py show <id>`'s `recommended_buffer` and `steps` -
if `rules.oversell_buffer` is off in `config/agent.yaml`, that is the
answer, "whatever the prediction says."

## An item is `needs_human` and I do not know why

```bash
python3 tools/review.py show <id>
```
The event history names the exact reason - usually `hotel.rooms` is 0, or no
rate is configured for `reference_room_type` on that date. Both are config
fixes, not bugs.

## `tools/run.py --once --reconcile` says "nothing to reconcile"

That is correct, not broken, for any night that was `skipped` (no oversell
was ever recommended) - there is nothing to read back. Check
`python3 tools/review.py list --status skipped`.

## `python3 tools/*.py` says `ModuleNotFoundError: No module named 'core'`

You ran it with a Python that is not the repo's virtualenv, or from outside
the repo root. Use `make run` / `make doctor` / etc. (they call
`.venv/bin/python` for you), or run `.venv/bin/python tools/run.py` directly
from the repo root.

## Still stuck

`data/logs/*.jsonl` has every decision the agent made, in order, with a run
id. `python3 tools/review.py show <id>` has the full event trail for one
item. If neither explains it, that is a real bug - describe exactly what you
ran and what you expected, and ask.
