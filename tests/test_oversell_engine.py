"""Tests for tools/oversell_engine.py - pure functions, no I/O, no store.
See docs/how-it-works.md for the formulas these check against.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.oversell_engine import (Arrival, RiskConfig, arrival_from_dict, build_candidates,
                                   build_result_note, build_walk_plan, demo_fast_forward,
                                   format_money, noshow_distribution, noshow_scan,
                                   round_to_5, score_arrival, score_arrivals, walk_score)


def _cfg(**overrides) -> RiskConfig:
    agent = {
        "risk": {
            "base_pct": 8,
            "channel_pts": {"Direct": 0, "Corporate": -2, "Phone": 4, "Walk-in": -4},
            "ota_default_pts": 10,
            "guarantee_pts": {"guaranteed": -12, "not_guaranteed": 15},
            "lead_time": {"same_day_days": 1, "same_day_pts": -6, "short_days": 14,
                         "short_pts": 0, "medium_days": 60, "medium_pts": 5, "long_pts": 10},
            "loyalty_pct": -8, "uncontactable_pct": 8, "min_pct": 2, "max_pct": 95,
            "high_threshold": 50, "medium_threshold": 20,
        }
    }
    agent["risk"].update(overrides)
    return RiskConfig.from_agent_config(agent)


def _arrival(**kw) -> Arrival:
    base = dict(id="r1", guest_name="Test Guest", room_type="classic", channel="Direct",
               nights=1, loyalty=False, booked_days_ago=5, guaranteed=True, contactable=True)
    base.update(kw)
    return Arrival(**base)


def test_score_arrival_direct_guaranteed_short_lead_is_low_risk():
    a = _arrival(channel="Direct", guaranteed=True, booked_days_ago=5, loyalty=True)
    scored = score_arrival(a, _cfg(), late_cancellation_hours=48)
    assert scored.risk_pct == 2.0  # clamped at min_pct: 8+0-12+0-8 = -12
    assert "low risk" in scored.basis


def test_score_arrival_ota_unguaranteed_long_lead_uncontactable_is_high_risk():
    a = _arrival(channel="Expedia", guaranteed=False, booked_days_ago=74, loyalty=False,
                contactable=False)
    scored = score_arrival(a, _cfg(), late_cancellation_hours=48)
    assert scored.risk_pct == 51.0  # 8+10+15+10+0+8
    assert "elevated risk" in scored.basis
    assert "Expedia" in scored.basis
    assert "no way to reach the guest" in scored.basis


def test_score_arrival_channel_not_in_table_uses_ota_default():
    a = _arrival(channel="Airbnb", guaranteed=True, booked_days_ago=10)
    scored = score_arrival(a, _cfg(), late_cancellation_hours=48)
    # 8 (base) + 10 (ota default) - 12 (guaranteed) + 0 (short lead) = 6
    assert scored.risk_pct == 6.0


def test_score_arrival_clamps_to_max_pct():
    cfg = _cfg()
    a = _arrival(channel="Expedia", guaranteed=False, booked_days_ago=200, loyalty=False,
                contactable=False)
    scored = score_arrival(a, cfg, late_cancellation_hours=48)
    assert scored.risk_pct <= cfg.max_pct


def test_late_cancellation_inside_window_locks_risk_at_100():
    a = _arrival(status="cancelled", cancelled_hours_before_checkin=30)
    scored = score_arrival(a, _cfg(), late_cancellation_hours=48)
    assert scored is not None
    assert scored.risk_pct == 100.0
    assert "late-cancellation window" in scored.basis


def test_cancellation_outside_window_is_excluded_entirely():
    a = _arrival(status="cancelled", cancelled_hours_before_checkin=144)
    scored = score_arrival(a, _cfg(), late_cancellation_hours=48)
    assert scored is None


def test_score_arrivals_drops_none_entries():
    arrivals = [_arrival(id="a"), _arrival(id="b", status="cancelled",
                                          cancelled_hours_before_checkin=200)]
    scored = score_arrivals(arrivals, _cfg(), late_cancellation_hours=48)
    assert len(scored) == 1
    assert scored[0].id == "a"


def _scored(**kw):
    a = _arrival(**kw)
    return score_arrival(a, _cfg(), late_cancellation_hours=48)


def test_noshow_scan_floors_and_caps_at_3():
    arrivals = [_scored(id=str(i), channel="Expedia", guaranteed=False, booked_days_ago=90,
                        contactable=False) for i in range(6)]  # each 51% -> sum 3.06
    scan = noshow_scan(arrivals, rule_on=True, max_buffer=3, high_threshold=50)
    assert scan.predicted_no_shows == 3  # floor(3.06)
    assert scan.recommended_buffer == 3  # capped, would be min(3, 3)
    assert "capped" not in scan.steps[2]  # predicted == cap, not > cap


def test_noshow_scan_caps_below_predicted():
    arrivals = [_scored(id=str(i), channel="Expedia", guaranteed=False, booked_days_ago=90,
                        contactable=False) for i in range(10)]  # sum 5.1 -> predicted 5
    scan = noshow_scan(arrivals, rule_on=True, max_buffer=3, high_threshold=50)
    assert scan.predicted_no_shows == 5
    assert scan.recommended_buffer == 3
    assert "capped at 3" in scan.steps[2]


def test_noshow_scan_rule_off_forces_zero_whatever_the_prediction():
    arrivals = [_scored(id=str(i), channel="Expedia", guaranteed=False, booked_days_ago=90,
                        contactable=False) for i in range(10)]
    scan = noshow_scan(arrivals, rule_on=False, max_buffer=3, high_threshold=50)
    assert scan.recommended_buffer == 0
    assert "disabled by rule" in scan.steps[2]


def test_noshow_scan_low_risk_recommends_zero_as_a_success():
    arrivals = [_scored(id=str(i)) for i in range(3)]  # each 2% -> sum 0.06
    scan = noshow_scan(arrivals, rule_on=True, max_buffer=3, high_threshold=50)
    assert scan.recommended_buffer == 0
    assert scan.predicted_no_shows == 0
    assert "correct call" in scan.steps[2]


def test_walk_score_protects_loyalty_direct_and_long_stays():
    long_loyal = _scored(nights=4, loyalty=True, channel="Direct", booked_days_ago=60)
    short_ota = _scored(nights=1, loyalty=False, channel="Expedia", booked_days_ago=3,
                        guaranteed=True)
    assert walk_score(short_ota) < walk_score(long_loyal)


def test_build_candidates_excludes_no_show_and_cancelled_sorted_lowest_first():
    low = _scored(id="low", nights=1, channel="Walk-in", booked_days_ago=0)
    high = _scored(id="high", nights=4, loyalty=True, channel="Direct", booked_days_ago=60)
    resolved = score_arrival(_arrival(id="gone", status="no_show"), _cfg(),
                             late_cancellation_hours=48) if False else None
    # a no_show arrival never reaches score_arrival as "confirmed" scoring path in
    # this template (see docs/how-it-works.md #7) - build_candidates excludes by
    # status regardless of how it got there, so construct one directly:
    from dataclasses import replace
    gone = replace(low, id="gone", status="no_show")
    candidates = build_candidates([low, high, gone], _cfg())
    ids = [c.arrival.id for c in candidates]
    assert "gone" not in ids
    assert ids[0] == "low"  # lowest score first


def test_build_walk_plan_none_when_not_sold_out():
    arrivals = [_scored(id=str(i), channel="Expedia", guaranteed=False, booked_days_ago=90,
                        contactable=False) for i in range(6)]
    plan = build_walk_plan(arrivals, sold_out=False, buffer=0, recommended_buffer=3,
                           cfg=_cfg(), classic_rate=180, partner_name="Harborview Inn",
                           partner_multiplier=0.95, taxi=18, goodwill=50, currency="EUR")
    assert plan is None


def test_build_walk_plan_none_when_buffer_and_recommendation_both_zero():
    arrivals = [_scored(id=str(i)) for i in range(3)]
    plan = build_walk_plan(arrivals, sold_out=True, buffer=0, recommended_buffer=0,
                           cfg=_cfg(), classic_rate=180, partner_name="Harborview Inn",
                           partner_multiplier=0.95, taxi=18, goodwill=50, currency="EUR")
    assert plan is None


def test_build_walk_plan_costs_and_picks_the_least_disruptive_guest():
    low = _scored(id="low", nights=1, channel="Walk-in", booked_days_ago=0)
    high = _scored(id="high", nights=4, loyalty=True, channel="Direct", booked_days_ago=60)
    plan = build_walk_plan([low, high], sold_out=True, buffer=0, recommended_buffer=1,
                           cfg=_cfg(), classic_rate=200, partner_name="Harborview Inn",
                           partner_multiplier=0.95, taxi=18, goodwill=50, currency="EUR")
    assert plan is not None
    assert plan.buffer == 1
    assert plan.buffer_source == "recommended"
    assert plan.pick.arrival.id == "low"
    assert plan.partner_rate == round_to_5(200 * 0.95)
    assert plan.cost_per_guest == plan.partner_rate + 18 + 50
    assert plan.protected_revenue == 1 * 200
    # coverage ratio is always the real computed number, never an asserted "~4x"
    assert plan.coverage_ratio == plan.protected_revenue / plan.worst_case_cost


def test_build_walk_plan_set_buffer_beats_recommended_and_records_the_source():
    low = _scored(id="low")
    plan = build_walk_plan([low], sold_out=True, buffer=2, recommended_buffer=1,
                           cfg=_cfg(), classic_rate=180, partner_name="Harborview Inn",
                           partner_multiplier=0.95, taxi=18, goodwill=50, currency="EUR")
    assert plan.buffer == 2
    assert plan.buffer_source == "set"


def test_noshow_distribution_sums_to_one_and_matches_two_coin_case():
    dist = noshow_distribution([0.5, 0.5])
    assert abs(sum(dist) - 1.0) < 1e-9
    assert dist == [0.25, 0.5, 0.25]


def test_noshow_distribution_empty_risks_is_certain_zero():
    assert noshow_distribution([]) == [1.0]


def test_format_money_uses_the_configured_currency_never_hardcoded_eur():
    assert format_money(380, "EUR") == "EUR 380"
    assert format_money(250, "GBP") == "GBP 250"
    assert format_money(1999.5, "NOK") == "NOK 2,000"


def test_build_result_note_no_no_shows_with_buffer():
    guests = [_scored(id=str(i)) for i in range(3)]
    note = build_result_note(guests, buffer=2, classic_rate=180, currency="EUR",
                             partner_name="Harborview Inn")
    assert "nobody no-showed" in note
    assert "+2 oversell" in note


def test_build_result_note_no_no_shows_buffer_zero():
    guests = [_scored(id=str(i)) for i in range(3)]
    note = build_result_note(guests, buffer=0, classic_rate=180, currency="EUR",
                             partner_name="Harborview Inn")
    assert note == "All 3 arrivals showed - buffer 0 was the right call."


def test_build_result_note_no_shows_no_buffer_prices_the_loss():
    from dataclasses import replace
    guests = [_scored(id="a"), replace(_scored(id="b"), status="no_show")]
    note = build_result_note(guests, buffer=0, classic_rate=180, currency="EUR",
                             partner_name="Harborview Inn")
    assert "stood empty" in note
    assert "EUR 180" in note


def test_build_result_note_no_shows_with_buffer_absorbs_and_reports_zero_walks():
    from dataclasses import replace
    guests = [replace(_scored(id="a"), status="no_show"),
             replace(_scored(id="b"), status="no_show"), _scored(id="c")]
    note = build_result_note(guests, buffer=2, classic_rate=180, currency="EUR",
                             partner_name="Harborview Inn")
    assert "absorbed them" in note
    assert "Zero walks." in note


def test_build_result_note_walks_still_needed_when_buffer_exceeds_no_shows():
    from dataclasses import replace
    guests = [replace(_scored(id="a"), status="no_show"), _scored(id="b"), _scored(id="c")]
    note = build_result_note(guests, buffer=3, classic_rate=180, currency="EUR",
                             partner_name="Harborview Inn")
    # buffer 3 was published but only 1 no-show materialised: 2 rooms still short
    assert "2 guest(s) still had to be walked to Harborview Inn" in note


def test_demo_fast_forward_never_disagrees_with_the_note():
    guests = [_scored(id="high", channel="Expedia", guaranteed=False, booked_days_ago=90,
                      contactable=False),  # 51%
             _scored(id="low")]           # 2%
    resolved = demo_fast_forward(guests, threshold=50.0)
    statuses = {a.id: a.status for a in resolved}
    assert statuses["high"] == "no_show"
    assert statuses["low"] == "arrived"


def test_arrival_from_dict_matches_fixture_shape():
    data = json.loads((REPO_ROOT / "fixtures" / "inbound" / "sold-out-squeeze.json").read_text())
    arrivals = [arrival_from_dict(a) for a in data["arrivals"]]
    assert len(arrivals) == 12
    assert all(a.room_type in ("classic", "deluxe") for a in arrivals)
