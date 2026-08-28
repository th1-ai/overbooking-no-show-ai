# Measuring the benefit

## The roster case

**Overbooking & No-Show AI ("The Juggler"):** `+2%` Occupancy on sold-out
nights (revenue). "Empty rooms from no-shows are pure lost revenue;
uncontrolled overbooking is a guest-experience disaster. This threads the
needle with data."

That is the roster's own figure, not this repo's promise on top of it. It is
an occupancy gain on nights that would otherwise sell out and then lose
rooms to no-shows - not a claim about every night, and not a claim this
template measures for you automatically (see "Caveats" below).

## What `make report` shows

```bash
make report
python3 tools/report.py --json
```

- **Nights scanned**, and how many were sold out - the denominator for
  everything else.
- **No-oversell nights** - the "no oversell tonight, buffer 0 is correct"
  outcome. Restraint is a result worth counting, not a gap in the numbers.
- **Rooms recommended in total** vs **rooms actually published** - the gap
  between the two, while in `mode: shadow`, is exactly what going live would
  have changed. Once live, the gap is what a human declined to publish.
- **Waiting for a person** - the queue depth right now.
- **Rejected** - recommendations a duty manager discarded outright. A
  pattern here (the same kind of night rejected repeatedly) is a signal to
  revisit `config/agent.yaml: oversell.max_buffer` or the risk weights, not
  to keep overriding by hand.
- **Recent morning-after notes** - the honest record from
  `python3 tools/run.py --once --reconcile`: did the buffer pay off, did
  rooms stand empty, did anyone still have to be walked. This is the closest
  thing this repo has to a scorecard, and it is real, not modelled.

## Reading "rooms recommended" vs "rooms published" honestly

In `mode: shadow` (the default, and where every fresh clone starts), a
recommendation can be approved but never published - the guard blocks the
write regardless. `rooms_published_total` in shadow mode will stay at 0.
That is not the agent failing to work; it is the agent doing exactly what
shadow mode promises. Do not compare the two numbers as a performance metric
until `workflows/90-go-live.md` has actually been worked through.

## Caveats worth keeping in mind

- **The risk score is a formula, not a trained model.** It is built from
  real signals (channel, guarantee status, lead time, loyalty,
  contactability) with weights you can see and edit in `config/agent.yaml:
  risk:` - but it has not been fitted to your property's own history. Watch
  the morning-after notes for a few weeks; if predicted no-shows are
  consistently high or low against what actually happens, adjust the
  weights, not the buffer cap.
- **The occupancy gain only exists on nights that would otherwise be
  sold out.** A property that rarely sells out will see this agent
  correctly recommend "no oversell" most nights - see `docs/how-it-works.md`
  "Design decisions" #1 for why that is treated as a genuine result, not a
  quiet failure.
- **The coverage ratio in the cost card is the real number, always** - see
  `docs/how-it-works.md` "Design decisions" #6. Do not assume it is always
  comfortably above 1x; some nights it will not be, and the note says so.
- **Partner availability is never checked automatically** (open question in
  `docs/how-it-works.md` "Design decisions" #5). A published buffer assumes
  the walk partner has a room - confirm before you rely on it.
- **A short review history is not a track record.** A few weeks of shadow
  mode tells you whether the guardrails make sense for this property; it
  does not tell you what a full season of controlled overbooking would have
  earned. Go live deliberately, and keep watching `make report` after you
  do.
