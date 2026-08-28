#!/usr/bin/env python3
"""tools/doctor.py - is Overbooking & No-Show AI configured and reachable right now?

    make doctor
    python3 tools/doctor.py

Runs the generic core.doctor checks (python, deps, config, .env, hotel
identity, mode, adapters, the store) plus this agent's own: hotel.rooms is
set (capacity for the sold-out test), reference_room_type is configured, at
least one walk partner is configured, the risk weights are internally
consistent, the walk-partner protocol note has been filled in (the one
knowledge/ file this agent reads - see knowledge/README.md), both scheduled
jobs are wired up, and - when systems.pms.adapter is csv - reservations.csv
actually carries loyalty/booked_days_ago/guaranteed (see
docs/integrations.md "Connect your systems"). core.doctor's generic
"knowledge" and "llm provider" lines are re-worded below for this repo: the
former points at knowledge/property.example.md, which this agent never
reads; the latter is boilerplate about a model this agent never calls, see
docs/how-it-works.md "Design decisions" #0. Exits 0 when everything passed,
1 when a FAIL line needs fixing. Never a traceback.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.doctor import Check, FAIL, PASS, WARN, print_table, run_checks  # noqa: E402
from tools.oversell_engine import RiskConfig, walk_config_from_agent  # noqa: E402

#: The 3 reservations.csv columns the risk engine reads by exact lowercase
#: key (not the loose header-matching every other field gets - see
#: `core/adapters/pms_csv.py` and `tools/run.py::_reservation_to_arrival`),
#: and the default each one silently falls back to when it is missing.
_CSV_RISK_COLUMN_DEFAULTS = {
    "loyalty": "False (treated as not a loyalty member)",
    "booked_days_ago": "0 (treated as booked today)",
    "guaranteed": "False (treated as not guaranteed)",
}


def check_capacity(settings: Settings) -> Check:
    if settings.hotel.rooms <= 0:
        return Check("capacity", FAIL, "hotel.rooms is 0 or not set",
                     "Set hotel.rooms in config/hotel.yaml to your real room count - "
                     "it is how this agent decides whether tonight is sold out.")
    return Check("capacity", PASS, f"{settings.hotel.rooms} rooms")


def check_reference_room_type(settings: Settings) -> Check:
    rt = str(settings.agent_get("reference_room_type", "") or "")
    if not rt:
        return Check("reference room type", FAIL, "reference_room_type is not set",
                     "Set config/agent.yaml: reference_room_type to a real room type id "
                     "your PMS uses - the buffer is priced off its tonight rate.")
    return Check("reference room type", PASS, rt)


def check_walk_partners(settings: Settings) -> Check:
    wc = walk_config_from_agent(settings.agent)
    partners = (settings.agent_get("walk", {}) or {}).get("partners") or []
    if not partners:
        return Check("walk partners", FAIL, "walk.partners is empty",
                     "List at least one partner hotel in config/agent.yaml: walk.partners, "
                     "with a name and a rate_multiplier.")
    bad = [p.get("name", "?") for p in partners if not p.get("name") or "rate_multiplier" not in p]
    if bad:
        return Check("walk partners", FAIL, f"missing name or rate_multiplier: {', '.join(bad)}",
                     "Every entry under walk.partners needs a name and a rate_multiplier.")
    return Check("walk partners", PASS,
                 f"{len(partners)} partner(s), first choice: {wc['partner_name']} "
                 f"(x{wc['partner_multiplier']:g})")


def check_risk_weights(settings: Settings) -> Check:
    cfg = RiskConfig.from_agent_config(settings.agent)
    problems = []
    if cfg.min_pct >= cfg.max_pct:
        problems.append(f"risk.min_pct ({cfg.min_pct:g}) must be below risk.max_pct ({cfg.max_pct:g})")
    if cfg.medium_threshold >= cfg.high_threshold:
        problems.append(f"risk.medium_threshold ({cfg.medium_threshold:g}) must be below "
                        f"risk.high_threshold ({cfg.high_threshold:g})")
    if problems:
        return Check("risk weights", FAIL, "; ".join(problems),
                     "Fix the thresholds in config/agent.yaml: risk:.")
    return Check("risk weights", PASS,
                 f"{cfg.min_pct:g}-{cfg.max_pct:g}%, bands at {cfg.medium_threshold:g}/"
                 f"{cfg.high_threshold:g}%")


def check_oversell_cap(settings: Settings) -> Check:
    max_buffer = int((settings.agent_get("oversell", {}) or {}).get("max_buffer", 0))
    rule_on = bool((settings.agent_get("rules", {}) or {}).get("oversell_buffer", True))
    if max_buffer <= 0:
        return Check("oversell cap", FAIL, "oversell.max_buffer must be a positive integer",
                     "Set config/agent.yaml: oversell.max_buffer (the spec default is 3).")
    return Check("oversell cap", PASS,
                 f"capped at {max_buffer}, rule {'on' if rule_on else 'OFF - forces buffer 0'}")


def check_walk_partner_protocol() -> Check:
    path = REPO_ROOT / "knowledge" / "walk-partner-protocol.md"
    if not path.is_file():
        return Check("walk-partner protocol", WARN, "knowledge/walk-partner-protocol.md not created yet",
                     "cp knowledge/walk-partner-protocol.example.md "
                     "knowledge/walk-partner-protocol.md, then fill in the real contact and "
                     "account details your duty managers need at 22:00 - see knowledge/README.md.")
    return Check("walk-partner protocol", PASS, "knowledge/walk-partner-protocol.md present")


def check_schedule(settings: Settings) -> Check:
    jobs = settings.agent_get("schedule", {}) or {}
    missing = [name for name in ("scan", "reconcile") if name not in jobs]
    if missing:
        return Check("schedule", FAIL, f"missing job(s) in config/agent.yaml: schedule: {', '.join(missing)}",
                     "Both 'scan' and 'reconcile' must be listed under schedule: - see "
                     "config/agent.example.yaml.")
    return Check("schedule", PASS, f"{len(jobs)} job(s): {', '.join(jobs)}")


def check_csv_import_columns(settings: Settings) -> Check:
    """When `systems.pms.adapter` is `csv`, name any of `loyalty`,
    `booked_days_ago` or `guaranteed` missing from `data/imports/reservations.csv`
    - and the default the risk engine will silently assume for each one.
    SIMULATION.md finding 3: exporting only the documented header (before this
    fix) produced a buffer nearly 6x too high, with no warning at all."""
    if settings.systems.pms.adapter != "csv":
        return Check("csv import columns", PASS,
                     f"pms adapter is '{settings.systems.pms.adapter}', not csv - nothing to check")
    from core.adapters import get_pms
    try:
        adapter = get_pms(settings)
    except Exception as exc:  # noqa: BLE001 - the "pms adapter" check above already reports this
        return Check("csv import columns", WARN, f"could not open the csv adapter: {exc}"[:160],
                     "See the 'pms adapter' line above.")
    path = getattr(adapter, "dir", None)
    csv_path = (path / "reservations.csv") if path else None
    if csv_path is None or not csv_path.exists():
        return Check("csv import columns", WARN, "reservations.csv not found yet",
                     "Export reservations from your PMS to data/imports/reservations.csv - "
                     "see docs/integrations.md 'Connect your systems'.")
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        header = {(h or "").strip() for h in (csv.DictReader(fh).fieldnames or [])}
    missing = [f"'{col}' -> defaults to {default}"
              for col, default in _CSV_RISK_COLUMN_DEFAULTS.items() if col not in header]
    if missing:
        return Check("csv import columns", WARN,
                     f"reservations.csv is missing: {'; '.join(missing)}",
                     "These 3 columns are read by exact lowercase key, not fuzzy-matched like "
                     "the rest of reservations.csv - add them to your export exactly as "
                     "'loyalty', 'booked_days_ago', 'guaranteed'. See docs/integrations.md "
                     "'Connect your systems'.")
    return Check("csv import columns", PASS, "loyalty, booked_days_ago, guaranteed all present")


def _reword_generic_checks(checks: list[Check], settings: Settings) -> None:
    """core.doctor's `run_checks()` always adds a generic "knowledge" line
    (pointing at knowledge/property.example.md, which this repo's own
    knowledge/README.md says this agent never reads - see the
    'walk-partner protocol' line instead) and a generic "llm provider" line
    (which invites setting llm.provider "for real work" - wrong for an agent
    with no LLM step at all). Never edit core/doctor.py itself: fix the
    wording of these two lines in place, after `run_checks()` returns, so
    the family-wide check still runs but reports something true for this
    one agent. See SIMULATION.md findings 4 and 5."""
    for c in checks:
        if c.name == "knowledge" and c.status != PASS:
            c.detail = ("only example files - this agent reads knowledge/walk-partner-"
                       "protocol.md only (property.md, faq.md, signature.md are not used "
                       "here - see knowledge/README.md and the 'walk-partner protocol' "
                       "line below)")
            c.fix_hint = ("cp knowledge/walk-partner-protocol.example.md "
                          "knowledge/walk-partner-protocol.md and fill in the real contact "
                          "and account details your duty managers need at 22:00. Do not "
                          "bother with property.md - this agent never reads it.")
        elif c.name == "llm provider":
            c.status = PASS
            c.detail = (f"no LLM step, anywhere in this agent (docs/how-it-works.md "
                       f"\"Design decisions\" #0) - config/hotel.yaml still carries "
                       f"llm.provider: '{settings.llm.provider}' for consistency with the "
                       f"rest of the family, but nothing here ever reads it")
            c.fix_hint = ""


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        checks = run_checks(None) + [Check("config", FAIL, str(exc),
                                           "Fix config/hotel.yaml or config/agent.yaml.")]
        return print_table(checks, title="Overbooking & No-Show AI - doctor")

    checks = run_checks(settings, extra=[check_capacity, check_reference_room_type,
                                        check_walk_partners, check_risk_weights,
                                        check_oversell_cap, check_schedule,
                                        check_csv_import_columns])
    checks.append(check_walk_partner_protocol())
    _reword_generic_checks(checks, settings)
    return print_table(checks, title="Overbooking & No-Show AI - doctor")


if __name__ == "__main__":
    raise SystemExit(main())
