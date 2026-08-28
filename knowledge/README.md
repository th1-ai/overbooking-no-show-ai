# knowledge/

This folder is the agent's memory of your property. Most agents in this
family read these files before drafting anything a guest sees. **Overbooking
& No-Show AI is different: it has no LLM step at all** (see
docs/how-it-works.md "Design decisions" #0), so nothing in `knowledge/` is
ever read by code. `property.md`/`faq.md`/`signature.md` below are the
generic scaffold templates, shipped for consistency across the family, and
are not used by this repo. `walk-partner-protocol.md` is the one file that
actually matters here - not because the agent reads it (it does not), but
because your duty managers do, at 22:00, when a plan the agent prepared has
to actually happen.

## What to put here

| File | What it holds |
|---|---|
| `walk-partner-protocol.md` | **This agent's own - for humans, not code.** Real phone numbers, account codes and after-hours contacts for every partner in `config/agent.yaml: walk.partners`. See `knowledge/walk-partner-protocol.example.md`. |
| `property.md` | Generic scaffold template. Not read by this agent. |
| `faq.md` | Generic scaffold template. Not read by this agent. |
| `signature.md` | Generic scaffold template. Not read by this agent - The Juggler sends no messages and contacts no guest. |

Copy the file that actually matters here:

```bash
cp knowledge/walk-partner-protocol.example.md knowledge/walk-partner-protocol.md
```

`knowledge/*.md` is gitignored (the `.example.md` files are not), because your
property notes are yours. `make doctor`'s "walk-partner protocol" line only
checks the file exists - it does not, and cannot, check the phone number is
still right. That is on you.

## How to write it

**Write it the way you would brief the night manager on their first solo
shift.** A real phone number they can dial at 22:00, not "reception has the
details." Nobody but a human ever reads this file.

**Keep it current with `config/agent.yaml`.** If you add or drop a walk
partner in `config/agent.yaml: walk.partners`, update this file in the same
sitting - a partner listed in one place and not the other is exactly the
kind of gap that only shows up at the worst possible moment.

## Keeping it current

Whenever a walk plan actually gets used, ask the duty manager afterwards
whether the protocol note was right - the right phone number, the right
rate code, nobody put on hold. Fix the file the same day.
