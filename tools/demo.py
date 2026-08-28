#!/usr/bin/env python3
"""tools/demo.py - the whole loop on the bundled fixtures, zero credentials.

    make demo
    python3 tools/demo.py

Uses `load_settings(demo=True)`: mock provider, shadow mode, and the mock
adapter for every system, whatever config/hotel.yaml says - a demo can never
read a real PMS. Runs against its own database (`data/demo/demo.db`) so
running it twice always shows the same picture and never touches
`data/agent.db` (that is `make run`'s file).

The scan and the publish-guard behaviour are the real code path
(`tools/run.py`, `tools/review.py`). The "next morning" step is demo-only:
`oversell_engine.demo_fast_forward()` fabricates the >=50%-risk outcome on an
in-memory copy of tonight's arrivals so the story has an ending without a
second day passing - see docs/how-it-works.md "Design decisions" #7. It never
calls a PMS write.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings, sub_data_dir  # noqa: E402
from core.log import summary_line  # noqa: E402
from core.review import WriteBlocked, approve  # noqa: E402
from core.store import Store, StoreError  # noqa: E402
from tools.oversell_engine import build_result_note, demo_fast_forward, format_money, scored_arrival_from_dict  # noqa: E402
from tools.run import one_pass_scan  # noqa: E402

# Fixed so the demo never depends on the real wall-clock date - fixtures/hotel
# is dated around this anchor. Real runs use date.today().
DEMO_DATE = "2026-09-15"


def main() -> int:
    try:
        settings = load_settings(demo=True)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    demo_db = sub_data_dir("demo") / "demo.db"
    if demo_db.exists():
        demo_db.unlink()  # every `make demo` is a clean, repeatable run
    try:
        store = Store(settings, path=demo_db)
    except StoreError as exc:
        print(f"store error: {exc}", file=sys.stderr)
        return 1

    print(f"Overbooking & No-Show AI demo - Hotel Aurora, {DEMO_DATE}\n")
    print("Scan (tools/run.py --once):\n")

    from datetime import date as date_cls
    code, stats = one_pass_scan(settings, store, start_date=date_cls.fromisoformat(DEMO_DATE),
                               nights=1, dry_run=False)
    if code != 0:
        print("demo: scan did not finish cleanly", file=sys.stderr)
        return 1

    item = store.get_by_external("pms", DEMO_DATE)
    if item is None:
        print("demo: no recommendation was queued for the fixtures - check fixtures/hotel/", file=sys.stderr)
        return 1

    payload = item.payload or {}
    for line in payload.get("steps", []):
        print(f"  {line}")
    print()

    plan = payload.get("walk_plan")
    if plan:
        currency = plan.get("currency", settings.hotel.currency)
        print(f"Sold out. Walk plan ready - buffer {plan['buffer']} ({plan['buffer_source']}):")
        pick = plan.get("pick") or {}
        print(f"  First pick: {pick.get('guest_name', '-')} (score {pick.get('score', '-')})")
        print(f"  Why: {pick.get('why', '-')}")
        print(f"  Partner: {plan['partner_name']} at {format_money(plan['partner_rate'], currency)}, "
             f"+ {format_money(plan['taxi'], currency)} taxi + {format_money(plan['goodwill'], currency)} "
             f"goodwill = {format_money(plan['cost_per_guest'], currency)}/guest")
        print(f"  Protected revenue {format_money(plan['protected_revenue'], currency)} vs worst case "
             f"{format_money(plan['worst_case_cost'], currency)} ({plan['coverage_ratio']:.2f}x covered)")
        print(f"  P(at least one walk): {plan['walk_risk_pct']:.0f}%, expected net "
             f"{format_money(plan['expected_net'], currency)}")
    else:
        print("Not sold out, or no oversell recommended - no walk plan needed. That is a success, "
             "not an empty result.")
    print()

    print(f"Item {item.id} is '{item.review_status}'.")
    print("The approve-and-publish flow (shown as text - a demo never actually publishes):\n")
    print(f"  python3 tools/review.py approve {item.id}")
    print(f"  python3 tools/review.py publish")
    print("  -> in shadow mode this is blocked outright: 'approval kept', nothing written.")
    print("  -> workflows/80-review.md walks through this for a hotel's own Claude session.\n")

    if plan:
        print("Next morning (demo fast-forward, not a real PMS write):\n")
        scored = [scored_arrival_from_dict(a) for a in payload.get("arrivals", [])]
        resolved = demo_fast_forward(scored, threshold=50.0)
        note = build_result_note(resolved, buffer=0, classic_rate=payload.get("classic_rate", 0.0),
                                 currency=settings.hotel.currency, partner_name=plan["partner_name"])
        print(f"  {note}\n")
        print("  (buffer 0 above: nothing was ever approved or published in this demo run - "
             "the note shows what happens with no oversell in place. Approve, publish in a live "
             "run, then `python3 tools/run.py --once --reconcile` for the real morning-after note.)\n")

    counts = store.counts()
    waiting = sum(counts.get(s, 0) for s in ("pending_review", "needs_human"))
    print(f"{waiting} item(s) waiting for a person.")
    print("Nothing was published: mode is shadow, and demo never calls sheets.append() on "
         "anything but the fixtures.")
    print("Next: `make review` to see what is waiting, or read workflows/10-scan.md.\n")

    demo_stats = {"processed": stats.get("processed", 0), "drafted": stats.get("drafted", 0),
                 "sent": stats.get("sent", 0)}
    print(f"DEMO OK — {summary_line(demo_stats, settings.mode)}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
