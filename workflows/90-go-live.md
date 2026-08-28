# Workflow: shadow to live

Objective: decide, together with the hotel, whether The Juggler is ready to
publish an approved oversell buffer on its own instead of only recommending
one - and make the change safely if so.

This is the hotel's decision, never the agent's. Do not suggest it until the
checklist below is genuinely true, and when you do raise it, say plainly what
changes. **Going live never lets the agent walk a guest or contact one -
that stays a human action, in shadow or in live, always. This is structural,
not a config switch.**

## Checklist

- [ ] `make doctor` is clean (no `FAIL` lines). `warn` on `mode` is expected
      until you flip it.
- [ ] `config/hotel.yaml` has the real room count and currency, and
      `config/agent.yaml` has your real `reference_room_type` and
      `walk.partners` - not the shipped examples.
- [ ] `knowledge/walk-partner-protocol.md` exists and has real phone numbers
      and account codes a duty manager can actually use at 22:00.
- [ ] At least a handful of real `make run` scans have gone through the
      review queue (`workflows/80-review.md`), not just the demo fixtures -
      enough that the hotel trusts the risk scores and the buffer
      recommendation for real nights.
- [ ] The hotel has run `python3 tools/run.py --once --reconcile` on a few
      real nights and the morning-after notes matched what actually
      happened at the desk.
- [ ] `python3 tools/review.py stale` has been run once, to clear anything
      approved during shadow testing that is now out of date.

## Making the change

1. Edit `config/hotel.yaml`:
   ```yaml
   mode: live
   ```
2. `review.require_approval_for` still lists `publish` by default - it
   should. Going live means **an approved buffer actually gets published**,
   not that the agent starts publishing unapproved ones. There is no config
   that changes that.
3. Run `make doctor` again to confirm.
4. Run one real pass and manually watch a publish go through:
   ```bash
   make run
   python3 tools/review.py list
   python3 tools/review.py approve <id>
   python3 tools/review.py publish
   ```
5. Tell the hotel exactly what just changed: an approved buffer now actually
   writes to `data/exports/oversell_log.csv` (or the Google Sheet) the next
   time someone (or a scheduled job) runs `python3 tools/review.py publish` -
   it is still never automatic before that approval, and the walk itself is
   still always a human decision at the desk.

## Going back to shadow

```yaml
mode: shadow
```
in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env` for one run. Either
stops every publish on the next pass, mid-schedule, with no other change
required.
