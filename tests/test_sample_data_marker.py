"""Regression tests for SIMULATION.md finding 1 [BLOCKER] (2026-08-28 fix):
fixture data could be approved as if it were the hotel's own. A real (not
`make demo`) pass whose `systems.pms.adapter` is still the shipped `mock`
default now:

  1. tags every item it creates with payload `_sample: True` (family-wide,
     `core.store.Store.upsert_item` via `core.adapters.is_sample_source` -
     `item.is_sample` reads that back) - this repo does not re-implement the
     tagging, only consumes it,
  2. prints a `[SAMPLE DATA]` marker in `make review` (`tools/review.py`
     `list`/`show`), and
  3. writes "computed from the shipped sample fixtures, not your property"
     into the buffer/walk-plan output a duty manager actually reads
     (`draft.body`, and the `--dry-run` lines) - `tools/run.py`'s own
     addition on top of the family-wide fix.

`tests/conftest.py`'s autouse fixture isolates AGENT_CONFIG_DIR/AGENT_REPO_ROOT
for every test in this module.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings
from core.store import Store
from tools.review import cmd_list, cmd_show
from tools.run import SAMPLE_DATA_NOTE, one_pass_scan

DEMO_DATE = date(2026, 9, 15)


def _real_scan(tmp_path):
    """A real (non-demo) pass on a fresh clone: systems.pms.adapter is still
    the shipped `mock` default - exactly SIMULATION.md's "Day-one scenario"."""
    settings = load_settings()
    assert settings.systems.pms.adapter == "mock"  # the shipped default
    assert settings.demo is False  # this is the real path, not `make demo`
    store = Store(settings, path=tmp_path / "test.db")
    code, stats = one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=False)
    assert code == 0
    return settings, store


def test_a_real_pass_on_the_mock_default_tags_its_item_sample(tmp_path):
    settings, store = _real_scan(tmp_path)
    item = store.get_by_external("pms", DEMO_DATE.isoformat())
    store.close()
    assert item is not None
    assert item.is_sample is True
    assert item.payload.get("_sample") is True


def test_the_draft_a_duty_manager_reads_says_it_is_sample_data(tmp_path):
    settings, store = _real_scan(tmp_path)
    item = store.get_by_external("pms", DEMO_DATE.isoformat())
    store.close()
    assert item.draft is not None
    body = item.draft["body"]
    assert "[SAMPLE DATA]" in body
    assert "computed from the shipped sample fixtures, not your property" in body
    assert SAMPLE_DATA_NOTE in body


def test_make_review_list_shows_the_sample_marker(tmp_path, capsys):
    settings, store = _real_scan(tmp_path)
    capsys.readouterr()  # discard the scan's own stdout
    cmd_list(store, SimpleNamespace(status=None, limit=50))
    store.close()
    out = capsys.readouterr().out
    assert "[SAMPLE DATA]" in out


def test_review_show_prints_a_sample_warning_before_the_json(tmp_path, capsys):
    settings, store = _real_scan(tmp_path)
    item = store.get_by_external("pms", DEMO_DATE.isoformat())
    capsys.readouterr()
    cmd_show(store, SimpleNamespace(id=item.id))
    store.close()
    out = capsys.readouterr().out
    assert out.startswith("[SAMPLE DATA]")


def test_demo_mode_is_not_marked_sample_it_already_announces_itself(tmp_path):
    # `make demo` announces itself loudly ("Overbooking & No-Show AI demo -
    # Hotel Aurora...") and never shares data/agent.db with a real run, so it
    # is intentionally excluded from the `_sample` tag - see
    # `core.adapters.is_sample_source`.
    settings = load_settings(demo=True)
    store = Store(settings, path=tmp_path / "test.db")
    one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=False)
    item = store.get_by_external("pms", DEMO_DATE.isoformat())
    store.close()
    assert item.is_sample is False


def test_dry_run_sample_pass_prints_the_notice_and_writes_nothing(tmp_path, capsys):
    settings = load_settings()
    settings.dry_run = True
    store = Store(settings, path=tmp_path / "test.db")
    capsys.readouterr()
    code, stats = one_pass_scan(settings, store, start_date=DEMO_DATE, nights=1, dry_run=True)
    items = store.list_items(limit=1000)
    store.close()
    out = capsys.readouterr().out
    assert code == 0
    assert items == []  # --dry-run still writes nothing, sample data or not
    assert "[SAMPLE DATA]" in out
    assert SAMPLE_DATA_NOTE in out
