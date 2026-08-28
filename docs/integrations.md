# Connecting your systems

Every connector in this repo is one of three things, and the table says which.
We will not tell you an integration exists when it does not.

| Badge | Means |
|---|---|
| **built** | Written against the real API and tested against it. |
| **universal** | Works with any system through a common protocol: IMAP/SMTP, CSV, a webhook. |
| **stub** | Interface only. Calling it raises a clear error with a recipe for adding it. |

Check what is actually working on your machine at any time:

```bash
make doctor
```

**What Overbooking & No-Show AI actually uses.** Only two of the four
adapters below: **PMS** (reads tonight's arrivals, in-house count and the
`reference_room_type` rate - nothing here writes to your PMS, see
`docs/how-it-works.md` "Design decisions" #7) and **Sheets** (writes one
audit row per published buffer - the equivalent of the demo's "Written to the
channel manager (simulated in this demo)"). It does not use Email or
Messaging at all - this agent has no guest inbox, drafts nothing, and
contacts no one. `pos`, `accounting`, `reviews`, `calendar`, `payments`,
`procurement` and `locks` are unused stubs, same as every repo in this
family.

## Status

### PMS - `systems.pms.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Reads `fixtures/hotel/*.json`. What `make demo` uses. |
| `csv` | universal | a CSV export | Reads `data/imports/*.csv`. **Start here.** Works with every PMS. |
| `cloudbeds` | built | OAuth app + refresh token | Live reads and writes. |
| `cli` | universal | a JSON-speaking CLI | Advanced. Bridges to a vendor command line tool. |

**`csv` - the one that always works.** Export from your PMS and drop the files in
`data/imports/`:

- `reservations.csv` - `id, status, check_in, check_out, room_type_id,
  room_type_name, room_id, adults, children, source, total, balance, currency,
  guest_email, guest_first_name, guest_last_name, guest_phone, guest_country,
  loyalty, booked_days_ago, guaranteed`
- `guests.csv` - `id, first_name, last_name, email, phone, country, language, vip`
- `rooms.csv` - `id, name, max_occupancy, count, rank`
- `rates.csv` - `date, room_type_id, price, currency, min_los, available, closed`

Headers are matched loosely: `checkIn`, `check_in` and `Check In` all work, and
extra columns are kept. Dates must be `YYYY-MM-DD`. Only `reservations.csv` is
required; the rest add capability.

**`loyalty`, `booked_days_ago` and `guaranteed` are different from every other
column above: the risk engine (`tools/run.py::_reservation_to_arrival`) reads
them by their exact lowercase key, not the loose header matching every other
field gets.** Write them exactly as `loyalty`, `booked_days_ago`,
`guaranteed` - not `Loyalty`, not `loyaltyStatus`, not `Booked Days Ago`. A
missing or differently-cased column is not an error - the risk engine quietly
assumes the worst-case default (`loyalty=False`, `guaranteed=False`,
`booked_days_ago=0`), which can swing the recommended buffer by several
rooms. `guaranteed`/`loyalty` accept the same values as every other yes/no
column in this repo (`true`/`false`, `1`/`0`, `yes`/`no`, case-insensitive) -
a literal text cell that says `"false"` is read as `False`, not as `True`.
`make doctor` warns by name when `reservations.csv` is missing one of these
three columns.

In CSV mode the agent cannot write back to your PMS, so anything it wants to
change is appended to `data/exports/pms_writes.csv` with everything a person
needs to apply it by hand. That is a feature: it is how you check the agent's
judgement before you give it write access.

**`cloudbeds`.** Create an app in the Cloudbeds developer portal, authorise it
once against your property, and put the result in `.env`:

```
CLOUDBEDS_CLIENT_ID=
CLOUDBEDS_CLIENT_SECRET=
CLOUDBEDS_REFRESH_TOKEN=
CLOUDBEDS_PROPERTY_ID=
```

Scopes: `read:reservation`, `write:reservation`, `read:guest`, `read:room`,
`read:rate`, `write:rate`, `read:hotel`. The access token refreshes itself.

**`cli`.** If your PMS already has a command line tool that prints JSON, point at
it. See the profiles at the top of `core/adapters/pms_cli.py`.

### Email - `systems.email.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Reads `fixtures/inbound/*.eml` and `*.json`. |
| `imap` | universal | mailbox + app password | Any provider. **Start here.** |
| `gmail` | built | Google OAuth desktop client | Adds Gmail labels and threads. |

**`imap`.** In `.env`:

```
EMAIL_ADDRESS=reservations@example.com
EMAIL_PASSWORD=            # an APP password, never your login password
IMAP_HOST=imap.example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587              # 587 STARTTLS, 465 implicit TLS
```

Google, Microsoft and Fastmail all issue app-specific passwords. Two-factor stays
on and you can revoke the password without touching the account.

Replies carry `In-Reply-To` and `References`, so they land inside the guest's
existing thread rather than starting a new one.

**`gmail`.** Google Cloud Console: enable the Gmail API, configure the consent
screen, create an OAuth client of type **Desktop app**, download the JSON to
`credentials.json`. Then `pip install google-api-python-client google-auth-oauthlib`
and run `make doctor`; a browser opens once and writes `token.json`. Scopes:
`gmail.readonly`, `gmail.send`, `gmail.modify`.

### Messaging - `systems.messaging.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Reads `fixtures/inbound/messages.json`. |
| `unipile` | built | your own UniPile account | WhatsApp on your own number. |
| `webhook` | universal | any URL | POST to Zapier, Make, n8n, or your own endpoint. |

**`unipile`.** You create the account, you connect your number by QR code, you
own the credentials: `UNIPILE_DSN`, `UNIPILE_API_KEY`, `UNIPILE_ACCOUNT_ID`.
WhatsApp Business policy limits what you may send outside a guest-initiated
window; read your provider's rules before turning this on.

**`webhook`.** The simplest possible outbound: set `MESSAGING_WEBHOOK_URL` and
the agent POSTs `{chat_id, text, kind, hotel, sent_at}`. Your automation tool
delivers it however you like. Send-only.

### Sheets - `systems.sheets.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `csv` | universal | nothing | Every publish writes a row to `data/exports/oversell_log.csv`: date, buffer, partner, cost per guest, protected revenue, worst-case cost. |
| `google` | built | service account JSON | The same row, in a live shared spreadsheet. |

For `google`: enable the Sheets API, create a service account and a JSON key,
save it as `service_account.json`, and share your spreadsheet with the service
account's email address as an Editor. Set `systems.sheets.spreadsheet_id` to the
long id from the sheet's URL.

**Why Sheets, not a PMS write, for the sell limit.** `core/adapters/base.py`'s
`PMS` class has no "raise the sell limit beyond capacity" method - the closest
candidates (`set_rate`, `add_note`, `update_reservation`) are all scoped to a
room type or a single reservation, and a controlled-oversell buffer is a
night-level number, not either of those. The source this repo generalises
from calls its own write "simulated in this demo" for the same reason - there
is no common PMS primitive for it. `oversell_log.csv` is the honest,
always-working audit trail; getting that number into your actual channel
manager's sell limit is property-specific - see "Implement your own" below.

### Everything else

`pos`, `accounting`, `reviews`, `calendar`, `payments`, `procurement` and `locks`
are **stubs**: the interface exists, nothing is implemented. Calling one raises an
error that tells you exactly this. This agent does not call any of them - the
`walk.taxi_cost` and `walk.goodwill_credit` numbers are priced and logged, never
posted to a ledger (see `docs/safety.md`).

## Signals this agent needs, with no adapter

Your walk partner's rate multiplier, the taxi cost and the goodwill credit
are not things any PMS, email, messaging or Sheets API exposes - and unlike
`revenue-management-ai`'s comp rates or pace data, they do not change daily,
so this template does not invent a CSV-import convention for them either.
They live as plain config in `config/agent.yaml: walk:` - see
`docs/how-it-works.md` "The walk plan" and `knowledge/walk-partner-protocol.md`
for the human side (phone numbers, account codes) that no config file should
hold.

| Where | What |
|---|---|
| `config/agent.yaml: reference_room_type` | The room type id whose tonight rate is read straight from your PMS (`get_rates()`) - both the revenue the buffer protects and the base for the partner room. |
| `config/agent.yaml: walk.partners` | A ranked list of partner hotels: `name`, `rate_multiplier`. The plan always uses `partners[0]`; availability is never checked automatically - see `docs/how-it-works.md` "Design decisions" #5. |
| `config/agent.yaml: walk.taxi_cost` / `walk.goodwill_credit` | Flat amounts in `hotel.currency`, priced into every walk-plan cost card. |

## Implement your own

<a id="implement-your-own"></a>

The interface is small on purpose, and your Claude Code session can do this with
you in an afternoon. Open `claude` in this folder and paste:

> Read `docs/integrations.md#implement-your-own` and `core/adapters/base.py`.
> I need a PMS adapter for **<your system>**. Its API docs are at **<url>** and
> I have credentials in `.env` as `<VAR names>`. Copy `core/adapters/pms_csv.py`
> as the shape, implement `ping`, `capabilities` and the read methods first,
> register it in `core/adapters/__init__.py`, and stop before the write methods
> so I can check the reads with `make doctor`.

### The five steps

**1. Copy the closest existing adapter.**
`core/adapters/pms_csv.py` for a PMS, `email_imap.py` for a mailbox,
`messaging_webhook.py` for a chat channel. They are short and heavily commented.

**2. Implement `ping()` and `capabilities()` first.**

```python
def ping(self) -> HealthCheck:
    """Never raises. Returns ok=False with a fix_hint a hotel can act on."""

def capabilities(self) -> set[str]:
    """The method names that actually do something on this adapter."""
```

`make doctor` reads both. Getting them right first means the rest of the work has
a feedback loop.

**3. Implement the reads.** Map the vendor's fields onto the dataclasses in
`core/adapters/base.py` (`Reservation`, `Guest`, `RoomType`, `RateRow`,
`EmailMessage`, `ChatMessage`). Put anything you do not map into `.extra` rather
than dropping it. Dates are ISO `YYYY-MM-DD`. Money is a float in the hotel's
currency.

**4. Implement the writes, each with the guard.**

```python
from core.adapters.base import guarded_write

@guarded_write("pms_write")
def add_note(self, reservation_id: str, text: str) -> dict:
    ...
```

The decorator is not optional. Without it your adapter can write while the agent
is in shadow mode, which defeats the entire safety model. The action name should
be one of the values in `review.require_approval_for`.

**5. Register it.** One line in `core/adapters/__init__.py`:

```python
REGISTRY["pms"]["yoursystem"] = "core.adapters.pms_yoursystem:YourSystemPMS"
```

Then set `systems.pms.adapter: yoursystem` in `config/hotel.yaml` and run
`make doctor`.

### Rules that matter

- **`ping()` never raises.** It returns `HealthCheck(ok=False, ...)` with a hint.
  A broken adapter must still produce a readable doctor table.
- **Every write is decorated.** No exceptions.
- **Rate limits belong in the adapter.** Use `core/adapters/_http.py:RateLimiter`.
  Retry 429 and 5xx with backoff; never retry a 4xx.
- **Never log a credential.** `core/log.py` masks anything whose key looks like a
  secret, but do not rely on it.
- **Redact on ingestion.** Any guest-written text goes through
  `core.redact.redact()` before it is stored or shown to a model.
- **Write a test.** Copy `tests/test_core_adapters_mock_csv.py`. It should run
  with no network: feed your parser a fixture, check the dataclass that comes out.

### `core/` is shared

`core/` is identical in all 28 agents in this family. If you change something in
`core/`, keep it generic - a hotel-specific tweak belongs in `tools/` or in your
own adapter file, not in the shared runtime.
