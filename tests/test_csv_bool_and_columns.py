"""Regression tests for SIMULATION.md findings 2 and 3 (2026-08-28 fix).

Finding 2 [BLOCKER]: `tools/run.py::_reservation_to_arrival` used to build
`loyalty`/`guaranteed` with plain `bool(...)` on the raw CSV string - and
`bool("false")` is `True` in Python. Fixed by reusing
`core.adapters.pms_csv._bool`, the same case-insensitive truthy parser the
CSV adapter already uses for `guest_vip`/`closed`.

Finding 3 [MAJOR]: the documented `reservations.csv` header did not include
`loyalty`, `booked_days_ago` or `guaranteed`, so a hotel that exported
exactly the documented columns got a buffer nearly 6x too high with no
warning. Fixed by documenting the 3 columns (README "Connect your systems",
docs/integrations.md) and adding `tools/doctor.py::check_csv_import_columns`,
which names any of the 3 that are missing and the default each one assumes.

`tests/conftest.py`'s autouse fixture isolates AGENT_CONFIG_DIR/AGENT_REPO_ROOT
for every test in this module, so these never touch a hotel's own config or
`data/imports/`.
"""

from __future__ import annotations

import csv as csv_module
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters.base import Guest, Reservation
from core.adapters.pms_csv import CsvPMS
from core.config import load_settings, sub_data_dir
from core.doctor import PASS, WARN
from tools.doctor import check_csv_import_columns
from tools.run import _reservation_to_arrival


def _reservation(**extra) -> Reservation:
    return Reservation(id="r1", status="confirmed", check_in="2026-09-15",
                       check_out="2026-09-16", room_type_id="classic",
                       guest=Guest(email="guest@example.com"), extra=extra)


# --------------------------------------------------------------------------
# Finding 2: boolean parsing
# --------------------------------------------------------------------------
def test_string_false_is_parsed_as_false_not_python_truthy():
    # bool("false") is True in Python - the literal text a real CSV export
    # writes for "no" must not be read as "yes".
    arrival = _reservation_to_arrival(_reservation(loyalty="false", guaranteed="false",
                                                   booked_days_ago="5"))
    assert arrival.loyalty is False
    assert arrival.guaranteed is False


def test_common_true_variants_are_all_parsed_as_true():
    for value in ("true", "True", "1", "yes", "Y"):
        arrival = _reservation_to_arrival(_reservation(loyalty=value, guaranteed=value,
                                                       booked_days_ago="1"))
        assert arrival.loyalty is True, value
        assert arrival.guaranteed is True, value


def test_native_json_booleans_from_the_mock_adapter_still_work():
    # The bug never fired for `mock` fixtures (native bool, not strings) -
    # the fix must not regress that path.
    arrival = _reservation_to_arrival(_reservation(loyalty=True, guaranteed=False,
                                                   booked_days_ago=3))
    assert arrival.loyalty is True
    assert arrival.guaranteed is False


def test_end_to_end_csv_import_does_not_flip_string_booleans(tmp_path):
    # Reproduces SIMULATION.md's repro end to end: a real reservations.csv
    # on disk, read through the actual CsvPMS adapter, not a hand-built
    # Reservation.
    path = sub_data_dir("imports") / "reservations.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv_module.writer(fh)
        writer.writerow(["id", "status", "check_in", "check_out", "room_type_id", "source",
                         "guest_email", "loyalty", "booked_days_ago", "guaranteed"])
        writer.writerow(["r1", "confirmed", "2026-09-15", "2026-09-16", "classic", "Direct",
                         "guest@example.com", "false", "5", "false"])

    settings = load_settings()
    settings.systems.pms.adapter = "csv"
    pms = CsvPMS(settings, settings.systems.pms)
    res = pms.list_reservations("2026-09-15", "2026-09-15")[0]
    arrival = _reservation_to_arrival(res)
    assert arrival.loyalty is False
    assert arrival.guaranteed is False


# --------------------------------------------------------------------------
# Finding 3: undocumented / missing risk columns
# --------------------------------------------------------------------------
def _write_reservations_csv(header: list[str], row: list[str]) -> None:
    path = sub_data_dir("imports") / "reservations.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv_module.writer(fh)
        writer.writerow(header)
        writer.writerow(row)


def test_doctor_warns_by_name_when_csv_import_is_missing_risk_columns(tmp_path):
    # Exactly the reservations.csv header documented before this fix - no
    # loyalty / booked_days_ago / guaranteed.
    _write_reservations_csv(
        ["id", "status", "check_in", "check_out", "room_type_id", "room_type_name",
         "room_id", "adults", "children", "source", "total", "balance", "currency",
         "guest_email", "guest_first_name", "guest_last_name", "guest_phone", "guest_country"],
        ["r1", "confirmed", "2026-09-15", "2026-09-16", "classic", "Classic", "101", "2",
         "0", "Direct", "150", "0", "EUR", "guest@example.com", "A", "B", "", "GB"])

    settings = load_settings()
    settings.systems.pms.adapter = "csv"
    check = check_csv_import_columns(settings)
    assert check.status == WARN
    assert "loyalty" in check.detail
    assert "booked_days_ago" in check.detail
    assert "guaranteed" in check.detail


def test_doctor_passes_once_all_three_risk_columns_are_present(tmp_path):
    _write_reservations_csv(
        ["id", "check_in", "check_out", "room_type_id", "guest_email", "loyalty",
         "booked_days_ago", "guaranteed"],
        ["r1", "2026-09-15", "2026-09-16", "classic", "guest@example.com", "true", "5", "false"])

    settings = load_settings()
    settings.systems.pms.adapter = "csv"
    check = check_csv_import_columns(settings)
    assert check.status == PASS


def test_doctor_skips_the_check_entirely_when_the_pms_adapter_is_not_csv():
    settings = load_settings()  # shipped default: systems.pms.adapter is mock
    check = check_csv_import_columns(settings)
    assert check.status == PASS
    assert "not csv" in check.detail
