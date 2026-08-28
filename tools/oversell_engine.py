"""tools/oversell_engine.py - The Juggler's whole decision engine. Pure functions.

No I/O anywhere in this file: every function takes plain dataclasses in and
returns plain dataclasses out. `tools/run.py` is the only place that talks to
the PMS, the store, or the review guard. This split is what lets
`tools/demo.py` and every test in `tests/test_oversell_engine.py` exercise the
exact same code a real overnight scan does.

This agent has no LLM step - every number here is a formula or a threshold.
See docs/how-it-works.md "Design decisions" #0.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field

CANCELLED_STATUSES = {"cancelled", "canceled"}
RESOLVED_STATUSES = {"arrived", "no_show"}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def round_to_5(x: float) -> float:
    return round(x / 5) * 5


def format_money(amount: float, currency: str = "EUR") -> str:
    """Every human-facing amount goes through this - never a hard-coded symbol."""
    return f"{currency} {amount:,.0f}"


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
@dataclass
class RiskConfig:
    base_pct: float = 8
    channel_pts: dict[str, float] = field(default_factory=dict)
    ota_default_pts: float = 10
    guaranteed_pts: float = -12
    not_guaranteed_pts: float = 15
    same_day_days: int = 1
    same_day_pts: float = -6
    short_days: int = 14
    short_pts: float = 0
    medium_days: int = 60
    medium_pts: float = 5
    long_pts: float = 10
    loyalty_pct: float = -8
    uncontactable_pct: float = 8
    min_pct: float = 2
    max_pct: float = 95
    high_threshold: float = 50
    medium_threshold: float = 20

    @classmethod
    def from_agent_config(cls, agent: dict) -> "RiskConfig":
        r = agent.get("risk") or {}
        lt = r.get("lead_time") or {}
        g = r.get("guarantee_pts") or {}
        d = cls()
        return cls(
            base_pct=float(r.get("base_pct", d.base_pct)),
            channel_pts={str(k): float(v) for k, v in (r.get("channel_pts") or {}).items()},
            ota_default_pts=float(r.get("ota_default_pts", d.ota_default_pts)),
            guaranteed_pts=float(g.get("guaranteed", d.guaranteed_pts)),
            not_guaranteed_pts=float(g.get("not_guaranteed", d.not_guaranteed_pts)),
            same_day_days=int(lt.get("same_day_days", d.same_day_days)),
            same_day_pts=float(lt.get("same_day_pts", d.same_day_pts)),
            short_days=int(lt.get("short_days", d.short_days)),
            short_pts=float(lt.get("short_pts", d.short_pts)),
            medium_days=int(lt.get("medium_days", d.medium_days)),
            medium_pts=float(lt.get("medium_pts", d.medium_pts)),
            long_pts=float(lt.get("long_pts", d.long_pts)),
            loyalty_pct=float(r.get("loyalty_pct", d.loyalty_pct)),
            uncontactable_pct=float(r.get("uncontactable_pct", d.uncontactable_pct)),
            min_pct=float(r.get("min_pct", d.min_pct)),
            max_pct=float(r.get("max_pct", d.max_pct)),
            high_threshold=float(r.get("high_threshold", d.high_threshold)),
            medium_threshold=float(r.get("medium_threshold", d.medium_threshold)),
        )


# --------------------------------------------------------------------------
# plain data
# --------------------------------------------------------------------------
@dataclass
class Arrival:
    """One booking against tonight. `status` is "confirmed" at scan time;
    "cancelled"/"canceled" for a late cancellation (see `score_arrival`);
    "arrived"/"no_show" only after the fact (reconcile / demo fast-forward)."""

    id: str
    guest_name: str
    room_type: str
    channel: str
    nights: int
    loyalty: bool
    booked_days_ago: int
    guaranteed: bool
    contactable: bool
    status: str = "confirmed"
    cancelled_hours_before_checkin: float | None = None


@dataclass
class ScoredArrival(Arrival):
    risk_pct: float = 0.0
    basis: str = ""


def arrival_from_dict(raw: dict) -> Arrival:
    """Build an `Arrival` from a PMS `Reservation`-shaped dict or a
    fixtures/inbound/*.json arrival dict. Accepts either the flat inbound
    fixture shape or a `core.adapters.base.Reservation.extra` style dict."""
    known = {"id", "guest_name", "room_type", "channel", "nights", "loyalty",
            "booked_days_ago", "guaranteed", "contactable", "status",
            "cancelled_hours_before_checkin"}
    data = {k: v for k, v in raw.items() if k in known}
    return Arrival(
        id=str(data.get("id", "")),
        guest_name=str(data.get("guest_name", "")),
        room_type=str(data.get("room_type", "")),
        channel=str(data.get("channel", "Direct")),
        nights=int(data.get("nights", 1)),
        loyalty=bool(data.get("loyalty", False)),
        booked_days_ago=int(data.get("booked_days_ago", 0)),
        guaranteed=bool(data.get("guaranteed", False)),
        contactable=bool(data.get("contactable", True)),
        status=str(data.get("status", "confirmed")),
        cancelled_hours_before_checkin=data.get("cancelled_hours_before_checkin"),
    )


# --------------------------------------------------------------------------
# risk scoring
# --------------------------------------------------------------------------
def _lead_time(days_ago: int, cfg: RiskConfig) -> tuple[float, str]:
    if days_ago <= cfg.same_day_days:
        return cfg.same_day_pts, f"booked {days_ago} day(s) ago (same-day)"
    if days_ago <= cfg.short_days:
        return cfg.short_pts, f"booked {days_ago} days ago"
    if days_ago <= cfg.medium_days:
        return cfg.medium_pts, f"booked {days_ago} days ago"
    return cfg.long_pts, f"booked {days_ago} days out"


def _channel_pts(channel: str, cfg: RiskConfig) -> float:
    return cfg.channel_pts.get(channel, cfg.ota_default_pts)


def _basis_clauses(a: Arrival, cfg: RiskConfig, lead_text: str) -> list[str]:
    clauses = [
        f"{a.channel} booking" if a.channel in cfg.channel_pts else f"{a.channel} (third-party) booking",
        "guaranteed with a card" if a.guaranteed else "no guarantee on file",
        lead_text,
    ]
    if a.loyalty:
        clauses.append("loyalty member")
    if not a.contactable:
        clauses.append("no way to reach the guest")
    return clauses


def score_arrival(a: Arrival, cfg: RiskConfig, *, late_cancellation_hours: float) -> ScoredArrival | None:
    """Score one arrival. Returns `None` for a normal, in-time cancellation -
    it is not part of tonight's list at all. See docs/how-it-works.md "Design
    decisions" #8 for the late-cancellation rule."""
    base_fields = dict(id=a.id, guest_name=a.guest_name, room_type=a.room_type,
                       channel=a.channel, nights=a.nights, loyalty=a.loyalty,
                       booked_days_ago=a.booked_days_ago, guaranteed=a.guaranteed,
                       contactable=a.contactable, status=a.status,
                       cancelled_hours_before_checkin=a.cancelled_hours_before_checkin)
    if a.status in CANCELLED_STATUSES:
        hours = a.cancelled_hours_before_checkin
        if hours is None or hours > late_cancellation_hours:
            return None
        basis = (f"cancelled {hours:g}h before arrival - inside the "
                f"{late_cancellation_hours:g}h late-cancellation window, counted as a "
                f"no-show for tonight's capacity")
        return ScoredArrival(**base_fields, risk_pct=100.0, basis=basis)

    lead_pts, lead_text = _lead_time(a.booked_days_ago, cfg)
    pts = (cfg.base_pct + _channel_pts(a.channel, cfg)
          + (cfg.guaranteed_pts if a.guaranteed else cfg.not_guaranteed_pts)
          + lead_pts
          + (cfg.loyalty_pct if a.loyalty else 0.0)
          + (cfg.uncontactable_pct if not a.contactable else 0.0))
    risk = round(clamp(pts, cfg.min_pct, cfg.max_pct))
    band = ("elevated risk" if risk >= cfg.high_threshold
           else "some risk" if risk >= cfg.medium_threshold else "low risk")
    basis = ", ".join(_basis_clauses(a, cfg, lead_text)) + f" — {band}"
    return ScoredArrival(**base_fields, risk_pct=float(risk), basis=basis)


def score_arrivals(arrivals: list[Arrival], cfg: RiskConfig, *,
                   late_cancellation_hours: float) -> list[ScoredArrival]:
    out = []
    for a in arrivals:
        scored = score_arrival(a, cfg, late_cancellation_hours=late_cancellation_hours)
        if scored is not None:
            out.append(scored)
    return out


# --------------------------------------------------------------------------
# the recommendation - runOverbookScan
# --------------------------------------------------------------------------
@dataclass
class ScanResult:
    steps: list[str]
    predicted_no_shows: int
    recommended_buffer: int
    expected_risk: float
    allowed: bool
    high_risk_count: int
    arrivals_count: int


def noshow_scan(arrivals: list[ScoredArrival], *, rule_on: bool, max_buffer: int,
                high_threshold: float) -> ScanResult:
    n = len(arrivals)
    high = sum(1 for a in arrivals if a.risk_pct >= high_threshold)
    expected = sum(a.risk_pct / 100.0 for a in arrivals)
    predicted = math.floor(expected)

    step1 = (f"{n} arrival(s) scored on guarantee, contact history and booking pattern. "
            f"{high} carr{'ies' if high == 1 else 'y'} a no-show risk above {high_threshold:g}%.")
    step2 = f"Sum of probabilities: {expected:.2f} room(s) - floored to {predicted} for a safe buffer."

    if not rule_on:
        recommended = 0
        step3 = ("Controlled overbooking is disabled by rule - no oversell tonight, "
                "whatever the prediction says.")
    elif predicted <= 0:
        recommended = 0
        step3 = ("No oversell tonight - the expected no-shows don't add up to a whole "
                "room, so buffer 0 is the correct call.")
    else:
        recommended = min(predicted, max_buffer)
        capped = f" (capped at {max_buffer} by rule)" if predicted > max_buffer else ""
        step3 = f"Accept {recommended} extra booking(s) tonight{capped}."

    return ScanResult(steps=[step1, step2, step3], predicted_no_shows=predicted,
                      recommended_buffer=recommended, expected_risk=expected,
                      allowed=rule_on, high_risk_count=high, arrivals_count=n)


# --------------------------------------------------------------------------
# the walk plan - buildWalkPlan
# --------------------------------------------------------------------------
def walk_score(a: ScoredArrival) -> float:
    """Lower walks first. See docs/how-it-works.md "The walk plan" for why
    each term is signed the way it is."""
    return (a.nights * 4
           + (10 if a.loyalty else 0)
           + (6 if a.channel == "Direct" else 0)
           + min(a.booked_days_ago, 30) / 10
           + a.risk_pct / 20)


def walk_reasons(a: ScoredArrival, cfg: RiskConfig) -> list[str]:
    reasons = []
    reasons.append("1-night stay - nothing left to disrupt after tonight" if a.nights <= 1
                   else f"{a.nights}-night stay - a move would break the rest of the booking")
    reasons.append("booked direct - our own guest, our own promise" if a.channel == "Direct"
                   else f"booked through {a.channel} - no direct relationship at stake")
    reasons.append("loyalty member - the last guest to move" if a.loyalty
                   else "no loyalty status on the profile")
    reasons.append(f"booked {a.booked_days_ago} days ago - not a long-planned trip"
                   if a.booked_days_ago <= 7 else f"booked {a.booked_days_ago} days ago")
    if a.risk_pct >= cfg.high_threshold:
        reasons.append(f"{a.risk_pct:g}% no-show risk - more likely than not to be a "
                       f"no-show, so holding a partner room for them could be wasted")
    elif a.risk_pct >= cfg.medium_threshold:
        reasons.append(f"{a.risk_pct:g}% no-show risk - a real chance they never arrive, "
                       f"which makes the relocation less certain")
    else:
        reasons.append(f"{a.risk_pct:g}% no-show risk - will almost certainly arrive, "
                       f"so the plan is worth preparing")
    return reasons


def walk_why(a: ScoredArrival) -> str:
    stay = "one night only" if a.nights <= 1 else f"{a.nights} nights"
    booked = "booked direct" if a.channel == "Direct" else a.channel
    loyalty = "loyalty member" if a.loyalty else "no loyalty status"
    return (f"{stay}, {booked}, {loyalty}, booked {a.booked_days_ago} days ago - the "
           f"least disruptive room to move, and at {a.risk_pct:g}% no-show risk they "
           f"will almost certainly be at the desk tonight, so the plan is worth having ready.")


@dataclass
class WalkCandidate:
    arrival: ScoredArrival
    score: float
    reasons: list[str]
    why: str


def build_candidates(arrivals: list[ScoredArrival], cfg: RiskConfig) -> list[WalkCandidate]:
    """Every arrival except one already resolved as no_show or cancelled - a
    guest who will not be there tonight cannot be walked. Sorted lowest-score
    first, tie-broken by risk then id, exactly as the source comment says."""
    excluded = {"no_show"} | CANCELLED_STATUSES
    pool = [a for a in arrivals if a.status not in excluded]
    out = [WalkCandidate(arrival=a, score=round(walk_score(a), 2),
                         reasons=walk_reasons(a, cfg), why=walk_why(a)) for a in pool]
    out.sort(key=lambda c: (c.score, c.arrival.risk_pct, c.arrival.id))
    return out


def noshow_distribution(risks: list[float]) -> list[float]:
    """Exact Poisson-binomial pmf over independent risks: dist[k] = P(exactly
    k of these arrivals fail to show), by iterative convolution."""
    dist = [1.0]
    for p in risks:
        nxt = [0.0] * (len(dist) + 1)
        for k, prob in enumerate(dist):
            nxt[k] += prob * (1 - p)
            nxt[k + 1] += prob * p
        dist = nxt
    return dist


@dataclass
class WalkPlan:
    pick: WalkCandidate | None
    ladder: list[WalkCandidate]
    buffer: int
    buffer_source: str
    predicted_no_shows: int
    arrivals_count: int
    partner_name: str
    partner_multiplier: float
    classic_rate: float
    partner_rate: float
    taxi: float
    goodwill: float
    cost_per_guest: float
    worst_case_walks: int
    worst_case_cost: float
    protected_revenue: float
    coverage_ratio: float
    walk_risk_pct: float
    expected_walks: float
    expected_walk_cost: float
    expected_recovered: float
    expected_net: float
    currency: str


def build_walk_plan(arrivals: list[ScoredArrival], *, sold_out: bool, buffer: int,
                    recommended_buffer: int, cfg: RiskConfig, classic_rate: float,
                    partner_name: str, partner_multiplier: float, taxi: float,
                    goodwill: float, currency: str) -> WalkPlan | None:
    """Returns `None` (no plan at all) when the night is not sold out, or when
    neither a published buffer nor a recommended one is above zero - see
    docs/how-it-works.md "The walk plan"."""
    effective = buffer if buffer > 0 else recommended_buffer
    if not sold_out or effective <= 0:
        return None
    buffer_source = "set" if buffer > 0 else "recommended"

    candidates = build_candidates(arrivals, cfg)
    pick = candidates[0] if candidates else None
    ladder = candidates[:3]

    partner_rate = round_to_5(classic_rate * partner_multiplier)
    cost_per_guest = partner_rate + taxi + goodwill

    risks = [a.risk_pct / 100.0 for a in arrivals]
    dist = noshow_distribution(risks)
    walk_risk_pct = sum(p for k, p in enumerate(dist) if effective - k < 0) * 100.0
    expected_walks = sum(p * max(0, effective - k) for k, p in enumerate(dist))
    worst_case_cost = effective * cost_per_guest
    protected_revenue = effective * classic_rate
    coverage_ratio = (protected_revenue / worst_case_cost) if worst_case_cost else 0.0
    expected_walk_cost = expected_walks * cost_per_guest
    expected_recovered = (effective - expected_walks) * classic_rate
    expected_net = expected_recovered - expected_walk_cost

    return WalkPlan(
        pick=pick, ladder=ladder, buffer=effective, buffer_source=buffer_source,
        predicted_no_shows=recommended_buffer, arrivals_count=len(arrivals),
        partner_name=partner_name, partner_multiplier=partner_multiplier,
        classic_rate=classic_rate, partner_rate=partner_rate, taxi=taxi, goodwill=goodwill,
        cost_per_guest=cost_per_guest, worst_case_walks=effective,
        worst_case_cost=worst_case_cost, protected_revenue=protected_revenue,
        coverage_ratio=coverage_ratio, walk_risk_pct=walk_risk_pct,
        expected_walks=expected_walks, expected_walk_cost=expected_walk_cost,
        expected_recovered=expected_recovered, expected_net=expected_net, currency=currency)


# --------------------------------------------------------------------------
# the morning-after note - buildFastForwardNote
# --------------------------------------------------------------------------
def build_result_note(arrivals_after: list[ScoredArrival], *, buffer: int,
                      classic_rate: float, currency: str, partner_name: str) -> str:
    """Four branches, verbatim in shape - see docs/how-it-works.md "The
    morning-after note". `arrivals_after` carries each arrival's real outcome
    (`status` already "arrived" or "no_show"/"cancelled")."""
    not_materialised = [a for a in arrivals_after
                        if a.status == "no_show" or a.status in CANCELLED_STATUSES]
    n = len(arrivals_after)
    k = len(not_materialised)
    names = ", ".join(a.guest_name for a in not_materialised)

    if k == 0 and buffer > 0:
        return (f"All {n} arrivals showed up - nobody no-showed, so the +{buffer} "
               f"oversell had to be honoured elsewhere: {buffer} guest(s) relocated "
               f"to {partner_name} exactly as the walk plan set out.")
    if k == 0:
        return f"All {n} arrivals showed - buffer 0 was the right call."
    if buffer <= 0:
        lost = k * classic_rate
        return (f"{k} predicted no-show(s) materialised ({names}). With no oversell "
               f"buffer those {k} room(s) stood empty on a sold-out night - "
               f"{format_money(lost, currency)} we could have resold.")
    resold = min(k, buffer)
    recovered = resold * classic_rate
    walks_needed = max(0, buffer - k)
    tail = "Zero walks." if walks_needed == 0 else (
        f"{walks_needed} guest(s) still had to be walked to {partner_name}.")
    return (f"{k} predicted no-show(s) materialised ({names}); the +{buffer} oversell "
           f"absorbed them - {resold} room(s) resold at {format_money(classic_rate, currency)}, "
           f"~{format_money(recovered, currency)} recovered. {tail}")


def demo_fast_forward(arrivals: list[ScoredArrival], *, threshold: float = 50.0) -> list[ScoredArrival]:
    """Demo-only: fabricate tomorrow morning's outcome on an in-memory copy,
    using the same >=threshold rule as the source, so the note and the
    (simulated) guest cards can never disagree. A real reconcile
    (`tools/run.py --once --reconcile`) reads real PMS statuses instead - see
    docs/how-it-works.md "Design decisions" #7. Never called outside
    `tools/demo.py`."""
    out = []
    for a in arrivals:
        if a.status in CANCELLED_STATUSES or a.status in RESOLVED_STATUSES:
            out.append(a)
            continue
        out.append(dataclasses.replace(a, status="no_show" if a.risk_pct >= threshold else "arrived"))
    return out


# --------------------------------------------------------------------------
# payload / draft (de)serialisation - shared by tools/run.py and
# tools/review.py so an edited buffer can be re-costed without a re-scan.
# --------------------------------------------------------------------------
def walk_config_from_agent(agent: dict) -> dict:
    walk = agent.get("walk") or {}
    partners = walk.get("partners") or []
    partner = partners[0] if partners else {"name": "your walk partner", "rate_multiplier": 1.0}
    return {
        "taxi": float(walk.get("taxi_cost", 18)),
        "goodwill": float(walk.get("goodwill_credit", 50)),
        "partner_name": str(partner.get("name", "your walk partner")),
        "partner_multiplier": float(partner.get("rate_multiplier", 1.0)),
    }


def arrival_dict(a: ScoredArrival) -> dict:
    return {"id": a.id, "guest_name": a.guest_name, "room_type": a.room_type,
           "channel": a.channel, "nights": a.nights, "loyalty": a.loyalty,
           "booked_days_ago": a.booked_days_ago, "guaranteed": a.guaranteed,
           "contactable": a.contactable, "status": a.status, "risk_pct": a.risk_pct,
           "basis": a.basis, "cancelled_hours_before_checkin": a.cancelled_hours_before_checkin}


def scored_arrival_from_dict(d: dict) -> ScoredArrival:
    return ScoredArrival(
        id=str(d.get("id", "")), guest_name=str(d.get("guest_name", "")),
        room_type=str(d.get("room_type", "")), channel=str(d.get("channel", "Direct")),
        nights=int(d.get("nights", 1)), loyalty=bool(d.get("loyalty", False)),
        booked_days_ago=int(d.get("booked_days_ago", 0)), guaranteed=bool(d.get("guaranteed", False)),
        contactable=bool(d.get("contactable", True)), status=str(d.get("status", "confirmed")),
        cancelled_hours_before_checkin=d.get("cancelled_hours_before_checkin"),
        risk_pct=float(d.get("risk_pct", 0.0)), basis=str(d.get("basis", "")))


def candidate_dict(c: WalkCandidate) -> dict:
    return {"guest_name": c.arrival.guest_name, "score": c.score, "reasons": c.reasons,
           "why": c.why, "risk_pct": c.arrival.risk_pct}


def plan_dict(plan: WalkPlan | None) -> dict | None:
    if plan is None:
        return None
    return {
        "buffer": plan.buffer, "buffer_source": plan.buffer_source,
        "predicted_no_shows": plan.predicted_no_shows,
        "pick": candidate_dict(plan.pick) if plan.pick else None,
        "ladder": [candidate_dict(c) for c in plan.ladder],
        "partner_name": plan.partner_name, "partner_multiplier": plan.partner_multiplier,
        "classic_rate": plan.classic_rate, "partner_rate": plan.partner_rate,
        "taxi": plan.taxi, "goodwill": plan.goodwill, "cost_per_guest": plan.cost_per_guest,
        "worst_case_walks": plan.worst_case_walks, "worst_case_cost": plan.worst_case_cost,
        "protected_revenue": plan.protected_revenue, "coverage_ratio": plan.coverage_ratio,
        "walk_risk_pct": plan.walk_risk_pct, "expected_walks": plan.expected_walks,
        "expected_walk_cost": plan.expected_walk_cost, "expected_recovered": plan.expected_recovered,
        "expected_net": plan.expected_net, "currency": plan.currency,
    }


def build_payload(*, target_date: str, capacity: int, otb: int, sold_out: bool,
                  classic_rate: float, reference_room_type: str, rate_missing: bool,
                  scan: ScanResult, scored: list[ScoredArrival], plan: WalkPlan | None) -> dict:
    return {
        "date": target_date, "capacity": capacity, "otb_rooms": otb, "sold_out": sold_out,
        "classic_rate": classic_rate, "reference_room_type": reference_room_type,
        "rate_missing": rate_missing, "steps": scan.steps,
        "predicted_no_shows": scan.predicted_no_shows,
        "recommended_buffer": scan.recommended_buffer, "expected_risk": scan.expected_risk,
        "high_risk_count": scan.high_risk_count, "rule_on": scan.allowed,
        "arrivals": [arrival_dict(a) for a in scored], "walk_plan": plan_dict(plan),
    }


def build_draft(scan: ScanResult, plan: WalkPlan | None) -> dict:
    draft = {"buffer": scan.recommended_buffer,
            "body": f"Recommended buffer: {scan.recommended_buffer} room(s)."}
    if plan is not None:
        draft["walk_plan"] = plan_dict(plan)
    return draft
