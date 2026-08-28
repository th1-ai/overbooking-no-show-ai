# Walk-partner protocol

This file is for your duty managers, not for the agent - The Juggler has no
LLM step and never reads this file in code (see docs/how-it-works.md "Design
decisions" #0). It exists so the plan the agent prepares at 18:00 can actually
be executed at 22:00 by whoever is on the desk that night, without hunting for
a phone number.

`make doctor`'s "walk-partner protocol" line just checks this file exists and
is not still the example - it does not read the contents.

Copy this to `knowledge/walk-partner-protocol.md` and fill in the real details
for every partner you listed in `config/agent.yaml: walk.partners`.

## Partner 1 - [Hotel name, matches config/agent.yaml: walk.partners[0].name]

- **Address / distance from us:** [walking or taxi minutes]
- **Reservations desk phone (direct line, not a switchboard):** [number]
- **After-hours contact:** [name / number / WhatsApp]
- **Account or rate code to quote:** [our negotiated rate code, if any]
- **How to confirm a room in under 5 minutes:** [call ahead; do not rely on
  their online availability - it is not checked automatically, see
  docs/how-it-works.md "Design decisions" #5]
- **Who invoices whom, and how often:** [monthly reconciliation contact]

## Partner 2 - [second entry, if you have one]

Same fields as above.

## The taxi account

- **Account name / number:** [your standing account with a local taxi firm]
- **How a duty manager books one at short notice:** [phone / app]
- **What "on us" covers:** [door to door, luggage, one trip]

## The goodwill credit

- **How it is applied:** [a note on the guest's folio for their next stay,
  valid for how long, who authorises it]
- **Who to tell:** [front office manager / revenue manager, so it is not a
  surprise on the next visit]

## When the plan does not go as expected

If the first-pick guest cannot be reached, or the partner has no room after
all, the walk ladder in the recommendation (`python3 tools/review.py show
<id>`) lists the next two candidates in order, with the same reasoning. If
none of the three works, that is a call for the duty manager, not the agent -
this template never walks a guest itself. See docs/safety.md.
