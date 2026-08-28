#!/usr/bin/env python3
"""tools/run.py - The Juggler's main loop: scan -> score -> recommend -> queue.
A separate --reconcile pass reads back what actually happened, next morning.

    python3 tools/run.py --once
    python3 tools/run.py --watch
    python3 tools/run.py --once --dry-run
    python3 tools/run.py --once --date 2026-09-15
    python3 tools/run.py --once --nights 7
    python3 tools/run.py --once --reconcile
    python3 tools/run.py --once --reconcile --date 2026-09-15

No LLM step anywhere in this agent - see docs/how-it-works.md "Design
decisions" #0. There is no exit code 3 (nothing ever pends on a model answer).
`--dry-run` computes and prints everything but writes nothing to the database,
even the recommendation item - only the `runs` observability row may still be
written, exactly as the family convention allows.

Exit codes: 0 ok, 1 a real error.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_pms  # noqa: E402
from core.adapters.base import AdapterError  # noqa: E402
from core.adapters.pms_csv import _bool as csv_bool  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.log import Run, get_logger, summary_line  # noqa: E402
from core.store import Store, StoreError  # noqa: E402
from tools.oversell_engine import (Arrival, RiskConfig, build_draft, build_payload,
                                   build_result_note, build_walk_plan, noshow_scan,
                                   score_arrivals, walk_config_from_agent)

log = get_logger("run")

CANCELLED_STATUSES = {"cancelled", "canceled"}
RESOLVED_STATUSES = {"arrived", "no_show"}
UNDECIDED_STATES = ("new", "pending_review", "needs_human")

#: Shown on the buffer/walk-plan output for a real (non-demo) pass whose PMS
#: adapter is still `mock` - see docs/how-it-works.md "Design decisions" #0
#: and SIMULATION.md finding 1. `core.adapters.sample_data_warning` already
#: prints a warning once per `Run`; this is the sentence that travels with
#: the actual recommendation a duty manager reads (the draft body, and the
#: --dry-run lines), so it is still visible on its own even if the run's
#: stderr warning scrolled off screen.
SAMPLE_DATA_NOTE = ("This buffer and walk plan are computed from the shipped sample "
                    "fixtures, not your property - systems.pms.adapter is 'mock'. "
                    "Connect a real PMS (docs/integrations.md) before treating this as "
                    "a real recommendation.")


def _is_sample_pass(settings) -> bool:
    """True for a real (non-demo) run whose PMS adapter is still the shipped
    `mock` default - see `core.adapters.is_sample_source`, which this mirrors
    for the one system this agent actually reads (`pms`)."""
    return (not getattr(settings, "demo", False)
           and getattr(getattr(settings.systems, "pms", None), "adapter", "") == "mock")


def _reservation_to_arrival(res) -> Arrival:
    x = res.extra or {}
    return Arrival(
        id=res.id, guest_name=res.guest.full_name or res.id, room_type=res.room_type_id,
        channel=res.source or "Direct", nights=res.nights,
        loyalty=csv_bool(x.get("loyalty", False)),
        booked_days_ago=int(x.get("booked_days_ago", 0)),
        guaranteed=csv_bool(x.get("guaranteed", False)),
        contactable=bool(res.guest.email or res.guest.phone),
        status=(res.status or "confirmed"),
        cancelled_hours_before_checkin=x.get("cancelled_hours_before_checkin"))


def _capacity_and_otb(pms, settings, target_date: str) -> tuple[int, int]:
    """Tonight's total physical capacity is `hotel.rooms`; on-the-books is
    every reservation in house tonight that has not cancelled or no-showed."""
    capacity = int(settings.hotel.rooms or 0)
    excluded = CANCELLED_STATUSES | {"no_show"}
    reservations = pms.list_reservations(target_date, target_date)
    otb = sum(1 for r in reservations
             if r.check_in <= target_date < r.check_out and r.status not in excluded)
    return capacity, otb


def compute_night(settings, pms, target_date: str) -> dict:
    """All the reads and all the arithmetic for one night - no store writes.
    Shared by the real scan, `--dry-run`, and `tools/demo.py`."""
    cfg = RiskConfig.from_agent_config(settings.agent)
    late_hours = float(settings.agent_get("late_cancellation_hours", 48))
    max_buffer = int((settings.agent_get("oversell", {}) or {}).get("max_buffer", 3))
    rule_on = bool((settings.agent_get("rules", {}) or {}).get("oversell_buffer", True))
    reference_room_type = str(settings.agent_get("reference_room_type", ""))

    capacity, otb = _capacity_and_otb(pms, settings, target_date)
    if capacity <= 0:
        return {"date": target_date, "capacity_missing": True}

    raw = pms.list_reservations(target_date, target_date)
    arrivals = [_reservation_to_arrival(r) for r in raw if r.check_in == target_date]
    scored = score_arrivals(arrivals, cfg, late_cancellation_hours=late_hours)
    scan = noshow_scan(scored, rule_on=rule_on, max_buffer=max_buffer,
                       high_threshold=cfg.high_threshold)
    sold_out = capacity > 0 and otb >= capacity

    rate_rows = pms.get_rates(target_date, target_date, room_type=reference_room_type)
    classic_rate = float(rate_rows[0].price) if rate_rows else 0.0
    wc = walk_config_from_agent(settings.agent)
    rate_missing = sold_out and scan.recommended_buffer > 0 and classic_rate <= 0

    plan = None
    if not rate_missing:
        plan = build_walk_plan(scored, sold_out=sold_out, buffer=0,
                               recommended_buffer=scan.recommended_buffer, cfg=cfg,
                               classic_rate=classic_rate, partner_name=wc["partner_name"],
                               partner_multiplier=wc["partner_multiplier"], taxi=wc["taxi"],
                               goodwill=wc["goodwill"], currency=settings.hotel.currency)

    return {"date": target_date, "capacity_missing": False, "capacity": capacity, "otb": otb,
           "sold_out": sold_out, "scan": scan, "scored": scored, "classic_rate": classic_rate,
           "reference_room_type": reference_room_type, "rate_missing": rate_missing, "plan": plan}


def _draft_with_sample_note(scan, plan, *, sample: bool) -> dict:
    """`build_draft()` plus `SAMPLE_DATA_NOTE` when this pass is reading the
    shipped `mock` fixtures for real (not `make demo`) - the marker that
    travels with the recommendation itself, not just the run's stderr
    warning. See `core.adapters.sample_data_warning` for the run-level one
    and `store.upsert_item`'s `_sample` payload tag that `tools/review.py`
    shows as `[SAMPLE DATA]`."""
    draft = build_draft(scan, plan)
    if sample:
        draft["body"] = f"[SAMPLE DATA] {draft['body']} {SAMPLE_DATA_NOTE}"
    return draft


def scan_night(settings, store, pms, target_date: str) -> str:
    """Queue or update tonight's recommendation item. Returns the result
    string used for the run's stats counters. See docs/how-it-works.md
    "Idempotency and pinning" for exactly when a re-scan is allowed to touch
    an existing item and when it must leave it alone."""
    sample = _is_sample_pass(settings)
    computed = compute_night(settings, pms, target_date)
    if computed["capacity_missing"]:
        item = store.upsert_item("pms", target_date, kind="oversell_recommendation",
                                 payload={"date": target_date})
        if item.review_status == "new":
            store.transition(item.id, "needs_human", "agent",
                             {"reason": "hotel.rooms is not configured - cannot tell if "
                                       "tonight is sold out"})
            return "needs_human"
        return "pinned"

    scan, plan = computed["scan"], computed["plan"]
    payload = build_payload(target_date=target_date, capacity=computed["capacity"],
                            otb=computed["otb"], sold_out=computed["sold_out"],
                            classic_rate=computed["classic_rate"],
                            reference_room_type=computed["reference_room_type"],
                            rate_missing=computed["rate_missing"], scan=scan,
                            scored=computed["scored"], plan=plan)
    # `store.upsert_item` already tags `_sample` on the payload for us when
    # the source's adapter is mock outside demo (core.adapters.is_sample_source)
    # - this repo only needs to carry the same fact onto the human-facing draft.
    item = store.upsert_item("pms", target_date, kind="oversell_recommendation", payload=payload)

    if item.review_status == "new":
        store.set_fields(item.id, draft=_draft_with_sample_note(scan, plan, sample=sample),
                         intent="oversell_recommendation")
        if computed["rate_missing"]:
            store.transition(item.id, "needs_human", "agent",
                             {"reason": f"no rate configured for reference_room_type "
                                       f"'{computed['reference_room_type']}' - cannot cost "
                                       f"the walk plan"})
            return "needs_human"
        if plan is not None:
            store.transition(item.id, "pending_review", "agent")
            return "pending_review"
        store.transition(item.id, "skipped", "agent",
                         {"reason": "no oversell tonight" if scan.recommended_buffer == 0
                                   else "night is not sold out"})
        return "skipped"

    if item.review_status in UNDECIDED_STATES[1:]:  # pending_review, needs_human: still live
        store.set_fields(item.id, draft=_draft_with_sample_note(scan, plan, sample=sample))
        return "updated"

    return "pinned"  # approved / edited / sending / sent / rejected / stale / skipped


def reconcile_night(settings, store, pms, target_date: str) -> str | None:
    """Read-only: the property's own PMS already marked each arrival
    `arrived` or `no_show` by the time this runs (see docs/how-it-works.md
    "Design decisions" #7). Logs the result note; writes nothing anywhere."""
    item = store.get_by_external("pms", target_date)
    if item is None or item.review_status == "skipped":
        return None  # nothing was recommended for this night - nothing to reconcile
    payload = item.payload or {}
    plan = payload.get("walk_plan") or {}
    buffer = int(plan.get("buffer", 0)) if item.review_status in ("sent", "approved", "edited") else 0
    classic_rate = float(payload.get("classic_rate", 0.0))
    partner_name = str(plan.get("partner_name", "your walk partner"))

    from tools.oversell_engine import ScoredArrival, scored_arrival_from_dict
    raw = pms.list_reservations(target_date, target_date)
    real_status = {r.id: (r.status or "confirmed") for r in raw}
    arrivals = [scored_arrival_from_dict(a) for a in payload.get("arrivals", [])]
    resolved = [ScoredArrival(**{**vars(a), "status": real_status.get(a.id, a.status)})
               for a in arrivals]

    note = build_result_note(resolved, buffer=buffer, classic_rate=classic_rate,
                             currency=settings.hotel.currency, partner_name=partner_name)
    log.info("reconciled", date=target_date, note=note)
    print(f"{target_date}: {note}")
    return note


def one_pass_scan(settings, store, *, start_date, nights: int, dry_run: bool) -> tuple[int, dict]:
    stats = {"processed": 0, "drafted": 0, "sent": 0, "pending_review": 0,
            "needs_human": 0, "skipped": 0, "updated": 0, "pinned": 0}
    pms = get_pms(settings)
    sample = _is_sample_pass(settings)
    with Run("scan", settings, store) as run:
        for i in range(nights):
            d = (start_date + timedelta(days=i)).isoformat()
            if dry_run:
                computed = compute_night(settings, pms, d)
                stats["processed"] += 1
                if computed.get("capacity_missing"):
                    print(f"{d}: hotel.rooms is not configured - cannot tell if tonight is sold out")
                    continue
                if sample:
                    print(f"{d}: [SAMPLE DATA] {SAMPLE_DATA_NOTE}")
                for line in computed["scan"].steps:
                    print(f"{d}: {line}")
                plan = computed["plan"]
                if plan is not None:
                    stats["drafted"] += 1
                    who = plan.pick.arrival.guest_name if plan.pick else "-"
                    print(f"{d}: walk plan ready - first pick {who}, "
                         f"cost/guest {plan.cost_per_guest:.0f} {plan.currency}")
                continue
            result = scan_night(settings, store, pms, d)
            stats["processed"] += 1
            stats[result] = stats.get(result, 0) + 1
            if result in ("pending_review", "needs_human"):
                stats["drafted"] += 1
            log.info("scanned", date=d, result=result)
        run.stats = dict(stats)
    return 0, stats


def one_pass_reconcile(settings, store, *, start_date, nights: int) -> tuple[int, dict]:
    stats = {"processed": 0, "drafted": 0, "sent": 0, "reconciled": 0, "nothing_to_reconcile": 0,
            "notes": []}
    pms = get_pms(settings)
    with Run("reconcile", settings, store) as run:
        for i in range(nights):
            d = (start_date + timedelta(days=i)).isoformat()
            stats["processed"] += 1
            note = reconcile_night(settings, store, pms, d)
            if note is not None:
                stats["reconciled"] += 1
                stats["notes"].append(f"{d}: {note}")
            else:
                stats["nothing_to_reconcile"] += 1
        run.stats = dict(stats)
    return 0, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--once", action="store_true", help="run a single pass (default)")
    mode_group.add_argument("--watch", action="store_true",
                            help="keep running on the configured interval")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute and print everything, write nothing, even in live mode")
    parser.add_argument("--reconcile", action="store_true",
                        help="read back what actually happened instead of scanning forward")
    parser.add_argument("--date", default=None,
                        help="YYYY-MM-DD (default: today for a scan, yesterday for --reconcile)")
    parser.add_argument("--nights", type=int, default=None,
                        help="how many nights (default: agent.yaml horizon_nights for a scan, 1 "
                             "for --reconcile)")
    parser.add_argument("--poll-seconds", type=int, default=None,
                        help="override the --watch interval (default: agent.yaml or 3600)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    def run_once() -> tuple[int, dict]:
        if args.reconcile:
            start = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
            return one_pass_reconcile(settings, store, start_date=start, nights=args.nights or 1)
        start = date.fromisoformat(args.date) if args.date else date.today()
        nights = args.nights or int(settings.agent_get("horizon_nights", 1))
        return one_pass_scan(settings, store, start_date=start, nights=nights, dry_run=args.dry_run)

    store = Store(settings)
    try:
        if args.watch:
            poll_seconds = args.poll_seconds or int(settings.agent_get("poll_seconds", 3600))
            while True:
                code, stats = run_once()
                print(summary_line(stats, settings.mode))
                if code != 0:
                    return code
                time.sleep(poll_seconds)
        code, stats = run_once()
        print(summary_line(stats, settings.mode))
        return code
    except AdapterError as exc:
        print(f"integration error: {exc}", file=sys.stderr)
        print("Run `make doctor` to see what is missing and how to fix it.", file=sys.stderr)
        return 1
    except StoreError as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
