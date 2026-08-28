#!/usr/bin/env python3
"""tools/report.py - what the agent recommended, and what actually happened.

    make report
    python3 tools/report.py --json

Everything here is computed from `data/agent.db` - nothing phoned home, and
there is no LLM spend to report (see docs/how-it-works.md "Design decisions"
#0). See docs/benefits.md for what each number means and its honest caveats.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings  # noqa: E402
from core.review import queue_summary  # noqa: E402
from core.store import Store, StoreError, _unjson  # noqa: E402


def build_report(store: Store) -> dict:
    counts = store.counts()
    queue = queue_summary(store)
    total = sum(counts.values())

    nights = store.list_items(kind="oversell_recommendation", limit=10000)
    recommended_total = 0
    published_total = 0
    no_oversell_nights = 0
    sold_out_nights = 0
    for item in nights:
        payload = item.payload or {}
        recommended_total += int(payload.get("recommended_buffer", 0) or 0)
        if payload.get("sold_out"):
            sold_out_nights += 1
        if payload.get("recommended_buffer", 0) == 0:
            no_oversell_nights += 1
        if item.review_status == "sent":
            published_total += int((item.draft or {}).get("buffer", 0) or 0)

    rows = store.db.execute(
        "SELECT stats_json FROM runs WHERE workflow='reconcile' ORDER BY finished_at DESC "
        "LIMIT 20").fetchall()
    recent_notes: list[str] = []
    for row in rows:
        stats = _unjson(row["stats_json"]) or {}
        recent_notes.extend(stats.get("notes", []))

    return {
        "total_items": total, "by_status": counts,
        "nights_scanned": len(nights), "sold_out_nights": sold_out_nights,
        "no_oversell_nights": no_oversell_nights,
        "rooms_recommended_total": recommended_total, "rooms_published_total": published_total,
        "waiting_on_human": queue["waiting_on_human"], "rejected": counts.get("rejected", 0),
        "recent_reconcile_notes": recent_notes[:10],
    }


def print_human(report: dict, mode: str) -> None:
    print("Overbooking & No-Show AI - report\n")
    print(f"Mode: {mode}")
    print(f"Nights scanned: {report['nights_scanned']} "
         f"({report['sold_out_nights']} sold out, {report['no_oversell_nights']} no oversell)")
    print(f"Waiting for a person: {report['waiting_on_human']}")
    print(f"Rooms recommended in total: {report['rooms_recommended_total']}")
    print(f"Rooms actually published: {report['rooms_published_total']}")
    print(f"Rejected: {report['rejected']}")
    print()
    print("By status:")
    for status, n in sorted(report["by_status"].items()):
        print(f"  {status:<16} {n}")
    if report["recent_reconcile_notes"]:
        print("\nRecent morning-after notes (python3 tools/run.py --once --reconcile):")
        for note in report["recent_reconcile_notes"]:
            print(f"  - {note}")
    if mode == "shadow":
        print("\nNote: mode is shadow, so 'rooms actually published' will stay 0 until you go "
             "live - see workflows/90-go-live.md.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    try:
        store = Store(settings)
    except StoreError as exc:
        print(f"store error: {exc}", file=sys.stderr)
        return 1
    try:
        report = build_report(store)
    finally:
        store.close()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_human(report, settings.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
