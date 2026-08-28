#!/usr/bin/env python3
"""tools/review.py - work the review queue: list / show / approve / edit /
reject / retry / publish / stale.

    python3 tools/review.py list [--status pending_review]
    python3 tools/review.py show <id>
    python3 tools/review.py approve <id> [--note "..."]
    python3 tools/review.py edit <id> --buffer 1 [--note "..."]
    python3 tools/review.py reject <id> --reason "too close a call"
    python3 tools/review.py retry <id>          # re-queue a failed publish
    python3 tools/review.py publish             # publish everything approved/edited
    python3 tools/review.py stale               # go-live step: clear the shadow-era queue

Only this tool writes `approved` / `edited` / `rejected` (core/review.py).
Only `publish` writes `sending` / `sent`. Nothing here bypasses `mode: shadow`
- see docs/safety.md. There is no free-text draft to edit here - `edit`
overrides the recommended buffer with a number the duty manager chooses, and
re-costs the walk plan against that number so what gets published matches
what they actually decided.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_sheets  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.review import WriteBlocked, approve, list_queue, reject, retry, show, stale_backlog  # noqa: E402
from core.store import Store, StoreError  # noqa: E402
from tools.oversell_engine import (RiskConfig, build_walk_plan, format_money, plan_dict,
                                   scored_arrival_from_dict, walk_config_from_agent)  # noqa: E402


def _print_item_line(item) -> None:
    payload = item.payload or {}
    draft = item.draft or {}
    marker = "[SAMPLE DATA] " if item.is_sample else ""
    print(f"  {item.id}  {item.review_status:<14} {payload.get('date', '-'):<12} "
         f"buffer {draft.get('buffer', '-')}  {marker}".rstrip())


def cmd_list(store, args) -> int:
    items = list_queue(store, status=args.status, kind="oversell_recommendation", limit=args.limit)
    if not items:
        print("Nothing is waiting for you.")
        return 0
    print(f"{len(items)} item(s) waiting:\n")
    for item in items:
        _print_item_line(item)
    if any(item.is_sample for item in items):
        print("\n[SAMPLE DATA] One or more items above were computed from the shipped "
             "sample fixtures, not your property - systems.pms.adapter is 'mock'. "
             "Connect a real PMS (docs/integrations.md) before approving them.")
    print("\nRun `python3 tools/review.py show <id>` for the full recommendation.")
    return 0


def cmd_show(store, args) -> int:
    try:
        detail = show(store, args.id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    payload = (detail.get("item") or {}).get("payload") or {}
    if payload.get("_sample"):
        print("[SAMPLE DATA] This item was computed from the shipped sample fixtures, not "
             "your property - systems.pms.adapter is 'mock'. Connect a real PMS "
             "(docs/integrations.md) before approving it.\n")
    print(json.dumps(detail, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_approve(store, args) -> int:
    before = store.get_item(args.id)
    if before is not None and before.is_sample:
        print(f"warning: {args.id} is [SAMPLE DATA] - the shipped sample fixtures, not your "
             "property. Connect a real PMS (docs/integrations.md) before this is a real "
             "recommendation.", file=sys.stderr)
    item = approve(store, args.id, note=args.note or "")
    print(f"approved {item.id} - buffer {item.draft.get('buffer')} - now in the publish queue")
    return 0


def cmd_edit(store, settings, args) -> int:
    item = store.get_item(args.id)
    if item is None:
        print(f"error: no item {args.id}", file=sys.stderr)
        return 1
    payload = item.payload or {}
    if not payload.get("arrivals"):
        print(f"error: {args.id} has nothing to re-cost (no arrivals on file)", file=sys.stderr)
        return 1

    cfg = RiskConfig.from_agent_config(settings.agent)
    wc = walk_config_from_agent(settings.agent)
    scored = [scored_arrival_from_dict(a) for a in payload["arrivals"]]
    plan = build_walk_plan(
        scored, sold_out=bool(payload.get("sold_out")), buffer=args.buffer,
        recommended_buffer=int(payload.get("recommended_buffer", 0)), cfg=cfg,
        classic_rate=float(payload.get("classic_rate", 0.0)), partner_name=wc["partner_name"],
        partner_multiplier=wc["partner_multiplier"], taxi=wc["taxi"], goodwill=wc["goodwill"],
        currency=settings.hotel.currency)

    old_buffer = (item.draft or {}).get("buffer")
    new_draft = dict(item.draft or {})
    new_draft["buffer"] = args.buffer
    new_draft["body"] = f"Buffer set by duty manager: {args.buffer} room(s) (was {old_buffer})."
    new_draft["walk_plan"] = plan_dict(plan)

    from core.review import edit as core_edit
    core_edit(store, args.id, new_draft, note=args.note or "")
    print(f"edited {item.id} - buffer {old_buffer} -> {args.buffer} - now in the publish queue")
    return 0


def cmd_reject(store, args) -> int:
    item = reject(store, args.id, reason=args.reason or "")
    print(f"rejected {item.id}")
    return 0


def cmd_retry(store, args) -> int:
    item = retry(store, args.id)
    print(f"queued {item.id} for another publish attempt")
    return 0


def cmd_publish(store, settings, args) -> int:
    """Publish everything approved or edited. The write is a guarded export
    row (`data/exports/oversell_log.csv`, or your Google Sheet) - see
    docs/integrations.md for why this agent uses Sheets, not a PMS write,
    for the sell limit. A buffer of 0 (a human approved "no oversell") has
    nothing to publish and is marked sent without touching an adapter."""
    claimed = store.claim_for_send(limit=args.limit)
    if not claimed:
        print("Nothing approved or edited is waiting to publish.")
        return 0
    sheets = get_sheets(settings)
    published, blocked, failed = 0, 0, 0
    for item in claimed:
        draft = item.draft or {}
        payload = item.payload or {}
        buffer = int(draft.get("buffer", 0))
        if buffer <= 0:
            store.mark_sent(item.id, None)
            print(f"recorded {item.id} - buffer 0, nothing to publish")
            published += 1
            continue
        plan = draft.get("walk_plan") or {}
        row = [payload.get("date", ""), buffer, plan.get("partner_name", ""),
              plan.get("cost_per_guest", ""), plan.get("protected_revenue", ""),
              plan.get("worst_case_cost", "")]
        try:
            result = sheets.append("oversell_log", [row], item=item)
        except WriteBlocked as exc:
            # Not a failure: the mode blocked it. The approval stands for go-live.
            store.transition(item.id, "approved", "agent", {"blocked": str(exc)[:200]})
            print(f"blocked {item.id} (approval kept): {exc}")
            blocked += 1
            continue
        except Exception as exc:  # noqa: BLE001 - record and move on, never crash the batch
            store.mark_send_failed(item.id, str(exc))
            print(f"failed {item.id}: {exc}")
            failed += 1
            continue
        store.mark_sent(item.id, result.get("message_id") if isinstance(result, dict) else None)
        cost = format_money(float(plan.get("cost_per_guest", 0) or 0), settings.hotel.currency)
        print(f"published {item.id} - buffer {buffer} to {plan.get('partner_name', '-')} "
             f"({cost}/guest if walked)")
        published += 1
    print(f"\n{published} published, {blocked} blocked, {failed} failed.")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="what is waiting for a human")
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--limit", type=int, default=50)

    p_show = sub.add_parser("show", help="full detail for one night's recommendation")
    p_show.add_argument("id")

    p_approve = sub.add_parser("approve", help="approve the recommended buffer unchanged")
    p_approve.add_argument("id")
    p_approve.add_argument("--note", default="")

    p_edit = sub.add_parser("edit", help="override the buffer, re-cost the walk plan, then queue it")
    p_edit.add_argument("id")
    p_edit.add_argument("--buffer", type=int, required=True)
    p_edit.add_argument("--note", default="")

    p_reject = sub.add_parser("reject", help="decline the recommendation - no oversell tonight")
    p_reject.add_argument("id")
    p_reject.add_argument("--reason", "--note", dest="reason", default="")

    p_retry = sub.add_parser("retry", help="re-queue a failed publish")
    p_retry.add_argument("id")

    p_publish = sub.add_parser("publish", help="publish every approved/edited buffer")
    p_publish.add_argument("--limit", type=int, default=20)

    sub.add_parser("stale", help="go-live step: mark everything still un-published as stale "
                                 "(the shadow-era queue was never sent and is out of date)")

    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    try:
        if args.command == "list":
            return cmd_list(store, args)
        if args.command == "show":
            return cmd_show(store, args)
        if args.command == "approve":
            return cmd_approve(store, args)
        if args.command == "edit":
            return cmd_edit(store, settings, args)
        if args.command == "reject":
            return cmd_reject(store, args)
        if args.command == "retry":
            return cmd_retry(store, args)
        if args.command == "publish":
            return cmd_publish(store, settings, args)
        if args.command == "stale":
            moved = stale_backlog(store)
            print(f"marked {len(moved)} item(s) stale. Nothing from before go-live will be published.")
            return 0
        parser.error(f"unknown command {args.command}")
        return 2
    except StoreError as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
