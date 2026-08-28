# Workflow: first-run setup

Objective: get Overbooking & No-Show AI ("The Juggler") from a fresh clone to
a working demo, then to real config, in one sitting.

## Steps

1. **Install and check.**
   ```bash
   make setup
   make doctor
   ```
   `make setup` creates the virtualenv, installs `requirements.txt`, and
   copies `.env.example` -> `.env` and every `config/*.example.yaml` ->
   `config/*.yaml` (only if those files do not exist yet - it never
   overwrites your own copies). `make doctor` will show a `FAIL` on "hotel
   identity" right after setup - that is expected, it means the property
   name is still the shipped placeholder. Everything else should be `ok` or
   `warn`.

2. **Run the demo.** No credentials needed.
   ```bash
   make demo
   ```
   Expect a sold-out night, two guests flagged over 50% no-show risk, a
   buffer of 2 recommended, a walk plan with a cost card, and the line
   `DEMO OK — 1 items processed, 1 drafted, 0 sent (shadow)`. If you do not
   see that, stop and read `workflows/99-troubleshooting.md` before going
   further.

3. **Fill in the property.** Edit `config/hotel.yaml`: `hotel.name`,
   `hotel.rooms` (your real room count - this is how the agent knows what
   "sold out" means for you), `hotel.currency`.

4. **Set the room type the buffer protects.** `config/agent.yaml:
   reference_room_type` must be a real room type id/code from your PMS - the
   buffer is priced off that room type's tonight rate. If you connect the
   `csv` PMS adapter, check the id matches what is in `rooms.csv`.

5. **Fill in your walk partner(s).** `config/agent.yaml: walk.partners` is a
   ranked list - name and `rate_multiplier` (their rate relative to yours).
   Then:
   ```bash
   cp knowledge/walk-partner-protocol.example.md knowledge/walk-partner-protocol.md
   ```
   Replace the placeholder contact details with the real phone numbers and
   account codes your duty managers will need at 22:00. See
   `knowledge/README.md`.

6. **Check the risk weights make sense for your property.** `config/agent.yaml:
   risk:` is a plain point system (channel, guarantee, lead time, loyalty,
   contactability) - read `docs/how-it-works.md` "The no-show /
   late-cancellation risk model" and adjust the numbers if your own history
   says a factor matters more or less than the shipped defaults.

7. **Connect your PMS (optional for now).** `systems.pms.adapter` in
   `config/hotel.yaml` starts as `mock`, which only ever sees the bundled
   fixtures. `docs/integrations.md` covers `csv` (works with any PMS export)
   and `cloudbeds`. Run `make doctor` after changing it.

8. **Re-check.**
   ```bash
   make doctor
   ```
   Once the property name, room count, reference room type and walk
   partners are real, everything should read `ok` except `mode: shadow`
   (expected - see `workflows/90-go-live.md`). Move on to
   `workflows/10-scan.md` to run the loop for real.

This agent has no LLM step - there is no provider to pick and no `interactive`
prompt to answer. See `docs/how-it-works.md` "Design decisions" #0 for why.
