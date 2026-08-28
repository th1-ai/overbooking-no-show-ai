"""Integration tests: the bundled fixtures, through tools/run.py's real
functions, with provider=mock and a throwaway store. No network, no
credentials - the same path `make demo` and a real overnight scan both take.
`tests/conftest.py` isolates AGENT_CONFIG_DIR/AGENT_REPO_ROOT for every test
in this module automatically (autouse fixture) - a hotel's own config/*.yaml
can never turn this suite red.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings
from core.review import WriteBlocked, approve
from core.store import Store
from tools.review import cmd_edit, cmd_publish
from tools.run import one_pass_reconcile, one_pass_scan

DEMO_DATE = date(2026, 9, 15)


def _store(tmp_path) -> tuple:
    settings = load_settings(demo=True)
    store = Store(settings, path=tmp_path / "test.db")
    return settings, store


def test_scan_produces_a_pending_review_item_on_the_sold_out_fixture(tmp_path):
    settings, store = _store(tmp_path)
    code, stats = one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=False)
    store.close()
    assert code == 0
    assert stats["pending_review"] == 1
    assert stats["drafted"] == 1


def test_shadow_mode_never_marks_anything_sent_before_a_human_acts(tmp_path):
    settings, store = _store(tmp_path)
    one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=False)
    counts = store.counts()
    store.close()
    assert counts.get("sent", 0) == 0
    assert counts.get("auto_sent", 0) == 0  # this agent never auto-sends anything, ever


def test_rerun_the_same_night_is_idempotent(tmp_path):
    settings, store = _store(tmp_path)
    one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=False)
    first = len(store.list_items(limit=1000))
    code, stats = one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=False)
    second = len(store.list_items(limit=1000))
    store.close()
    assert code == 0
    assert stats["updated"] == 1  # same undecided item refreshed, not duplicated
    assert first == second


def test_a_new_night_gets_a_fresh_item(tmp_path):
    from datetime import timedelta
    settings, store = _store(tmp_path)
    one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=False)
    first = len(store.list_items(limit=1000))
    # a night with no fixture data at all is still handled cleanly (capacity is
    # configured, but there are no arrivals - "no oversell" is a valid, celebrated
    # answer, not a crash)
    one_pass_scan(settings, store, start_date=DEMO_DATE + timedelta(days=1), nights=1,
                  dry_run=False)
    second = len(store.list_items(limit=1000))
    store.close()
    assert second == first + 1


def test_dry_run_writes_nothing_to_the_database(tmp_path):
    settings, store = _store(tmp_path)
    settings.dry_run = True
    code, stats = one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=True)
    items = store.list_items(limit=1000)
    store.close()
    assert code == 0
    assert stats["processed"] == 1
    assert items == []  # not even one row - a rehearsal writes nothing


def test_approve_then_publish_is_blocked_in_shadow_and_keeps_the_approval(tmp_path, capsys):
    from types import SimpleNamespace
    settings, store = _store(tmp_path)
    one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=False)
    item = store.list_items(status="pending_review", kind="oversell_recommendation", limit=1)[0]
    approve(store, item.id)
    assert settings.mode == "shadow"  # load_settings(demo=True) forces shadow

    cmd_publish(store, settings, SimpleNamespace(limit=20))
    updated = store.get_item(item.id)
    store.close()

    out = capsys.readouterr().out
    assert "approval kept" in out
    assert updated.review_status == "approved"  # never "failed"
    assert not updated.error


def test_edit_overrides_the_buffer_and_recosts_the_walk_plan(tmp_path):
    from types import SimpleNamespace
    settings, store = _store(tmp_path)
    one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=False)
    item = store.list_items(status="pending_review", kind="oversell_recommendation", limit=1)[0]
    original_buffer = item.draft["buffer"]

    cmd_edit(store, settings, SimpleNamespace(id=item.id, buffer=1, note="conservative call"))
    updated = store.get_item(item.id)
    store.close()

    assert updated.review_status == "edited"
    assert updated.draft["buffer"] == 1
    assert updated.draft["buffer"] != original_buffer
    assert updated.draft["walk_plan"]["buffer"] == 1
    assert updated.draft["walk_plan"]["buffer_source"] == "set"  # not "recommended" any more


def test_scan_after_approval_never_touches_the_pinned_draft(tmp_path):
    settings, store = _store(tmp_path)
    one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=False)
    item = store.list_items(status="pending_review", kind="oversell_recommendation", limit=1)[0]
    approve(store, item.id)
    pinned_draft = dict(item.draft)

    one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=False)
    still = store.get_item(item.id)
    store.close()

    assert still.review_status == "approved"  # a re-scan never reverts a human decision
    assert still.draft == pinned_draft  # and never rewrites what they approved


def test_capacity_not_configured_is_needs_human_not_a_crash(tmp_path):
    settings, store = _store(tmp_path)
    settings.hotel.rooms = 0
    code, stats = one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=False)
    item = store.get_by_external("pms", DEMO_DATE.isoformat())
    store.close()
    assert code == 0
    assert stats["needs_human"] == 1
    assert item.review_status == "needs_human"


def test_reconcile_reads_pms_status_and_never_writes_anything(tmp_path):
    settings, store = _store(tmp_path)
    one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=False)
    before = {i.id: i.review_status for i in store.list_items(limit=1000)}

    code, stats = one_pass_reconcile(settings, store, start_date=DEMO_DATE, nights=1)
    after = {i.id: i.review_status for i in store.list_items(limit=1000)}
    store.close()

    assert code == 0
    assert stats["reconciled"] == 1
    assert len(stats["notes"]) == 1
    assert before == after  # reconcile is read-only - not one status changed


def test_reconcile_with_nothing_recommended_is_a_clean_no_op(tmp_path):
    from datetime import timedelta
    settings, store = _store(tmp_path)
    code, stats = one_pass_reconcile(settings, store, start_date=DEMO_DATE + timedelta(days=30),
                                     nights=1)
    store.close()
    assert code == 0
    assert stats["nothing_to_reconcile"] == 1
    assert stats["notes"] == []
