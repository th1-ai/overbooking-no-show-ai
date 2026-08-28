"""Regression tests for SIMULATION.md findings 4 and 5 (2026-08-28 fix).

Finding 4 [MINOR]: core.doctor's generic "knowledge" line told a hotel to
fill in `knowledge/property.md`, contradicting this repo's own
`knowledge/README.md` ("not used by this repo"). Finding 5 [MINOR]: the
generic "llm provider" line invited setting `llm.provider` "for real work",
even though this agent has no LLM step at all.

`tools/doctor.py::_reword_generic_checks` fixes both after `core.doctor.run_checks()`
returns - core/doctor.py itself is never edited.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings
from core.doctor import PASS, run_checks
from tools.doctor import (_reword_generic_checks, check_capacity, check_csv_import_columns,
                          check_oversell_cap, check_reference_room_type, check_risk_weights,
                          check_schedule, check_walk_partners)


def _checks():
    settings = load_settings()
    checks = run_checks(settings, extra=[check_capacity, check_reference_room_type,
                                        check_walk_partners, check_risk_weights,
                                        check_oversell_cap, check_schedule,
                                        check_csv_import_columns])
    _reword_generic_checks(checks, settings)
    return {c.name: c for c in checks}


def test_llm_provider_line_leads_with_no_llm_step_before_any_generic_text():
    checks = _checks()
    llm = checks["llm provider"]
    assert llm.status == PASS
    assert llm.detail.startswith("no LLM step, anywhere")
    # the generic "set llm.provider ... for real work" invitation is gone
    assert "for real work" not in llm.detail
    assert not llm.fix_hint


def test_knowledge_line_never_points_a_fresh_hotel_at_property_md():
    checks = _checks()
    knowledge = checks["knowledge"]
    if knowledge.status != PASS:  # WARN on a fresh clone - only example files present
        assert "property.example.md" not in knowledge.fix_hint
        assert "walk-partner-protocol" in knowledge.fix_hint
