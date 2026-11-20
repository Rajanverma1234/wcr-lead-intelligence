"""Buying probability (0-100) and lead temperature - rule based and explainable.

Start at BASE, add/subtract the weights below for every rule that fires,
clamp to 0-100. Every rule that fires is written to Score_Breakdown with the
sentence that triggered it, so any score can be explained line by line.
"""
from __future__ import annotations

from .conflicts import ceiling_increased, remark_budgets
from .extractor import LATEST_REMARK, NA, REMARK_SOURCES

# ---- All weights in ONE place (easy to explain and tune) -------------------
WEIGHTS = {
    "base": 40,
    "negotiation": 30,             # negotiating / asked for final offer
    "visit_completed_or_confirmed": 25,
    "visit_tentative": 10,
    "clear_budget": 5,
    "budget_increased": 5,
    "specific_requirement": 5,     # BHK, number of units, specific project
    "requested_material": 5,       # payment plan, floor plan, inventory, ...
    "per_dependency": -10,         # family decision, sell property, waiting...
    "budget_mismatch": -20,        # cannot stretch
    "far_timeline": -25,           # LONG_DELAY: > 6 months away
    "not_interested": -40,         # not interested / do not follow up / no plan to buy
    "no_requirement": -20,         # nothing captured at all
}
# Timeline points by category (round 2). (max months, points) - first match wins.
# VAGUE / UNCERTAIN / NONE = 0. LONG_DELAY uses WEIGHTS["far_timeline"].
TIMELINE_POINTS = {
    "CONFIRMED": ((1, 20), (3, 10), (6, 5)),
    "TENTATIVE": ((1, 10), (3, 5), (6, 2)),
}
HOT_THRESHOLD = 70   # >= 70 Hot
WARM_THRESHOLD = 40  # 40-69 Warm, < 40 Cold
COLD_OVERRIDE_MONTHS = 12  # a LONG_DELAY of 12+ months is always Cold


def timeline_points(category: str, months: float | None) -> int:
    if category == "LONG_DELAY":
        return WEIGHTS["far_timeline"]
    if months is None:
        return 0
    for limit, points in TIMELINE_POINTS.get(category, ()):
        if months <= limit:
            return points
    return 0


def timeline_line(timeline_meta: dict) -> tuple[int, str]:
    """Points + an explicit Score_Breakdown line for the effective timeline."""
    resolution = timeline_meta["resolution"]
    eff = resolution["effective"]
    points = timeline_points(eff.category, eff.months_estimate)
    where = {"LATEST_REMARK": "latest", "PREVIOUS_REMARK": "previous remark"}.get(eff.source, "no remark")
    m = eff.months_estimate
    approx = f", ~{m:g} month{'' if m == 1 else 's'}" if m is not None else ""
    quote = f' "{eff.raw_text}"' if eff.raw_text else ""
    line = f"Timeline: {eff.category} ({where}{approx}){quote} → {points:+d} pts" if points else \
        f"Timeline: {eff.category} ({where}){quote} → 0 pts"
    ignored = resolution.get("ignored_previous")
    if ignored:
        line += f"; previous '{ignored.raw_text}' ignored for scoring"
    return points, line


def _short(sentence: str, n: int = 70) -> str:
    return sentence if len(sentence) <= n else sentence[: n - 1] + "…"


def score_lead(lead: dict, fields: dict, signals: dict) -> dict:
    """Return {'probability', 'temperature', 'breakdown' (list of strings)}."""
    score = WEIGHTS["base"]
    lines = [f"Base score: {WEIGHTS['base']}"]

    def apply(key, reason):
        nonlocal score
        score += WEIGHTS[key]
        lines.append(f"{WEIGHTS[key]:+d} {reason}")

    # Negotiation
    if signals.get("negotiation"):
        apply("negotiation", f'negotiation / final offer ("{_short(signals["negotiation"])}")')

    # Site visit
    visit = fields["Site_Visit_Status"]
    status = visit.meta.get("status")
    if status in ("Completed", "Confirmed"):
        apply("visit_completed_or_confirmed", f"site visit {status.lower()}")
    elif status == "Tentative":
        apply("visit_tentative", "site visit tentative")

    # Timeline (effective = latest remark if it says anything; previous never adds points then)
    timeline = fields["Timeline"]
    t_points, t_line = timeline_line(timeline.meta)
    score += t_points
    lines.append(t_line)
    category, months = timeline.meta.get("category"), timeline.meta.get("months")

    # Budget (only counts when the client said it in a remark, not just a CRM column)
    budget = fields["Budget"]
    if budget.value != NA and budget.source in REMARK_SOURCES:
        apply("clear_budget", f"clear budget stated: {budget.value}")
    # Budget increased = latest stated upper bound > previous stated upper bound (same currency).
    # Independent of the overlap check, which is used only for conflict flags.
    old, new = remark_budgets(lead)
    if budget.source == LATEST_REMARK and ceiling_increased(old, new):
        apply("budget_increased", f"budget increased: {old['value']} → {new['value']}")

    # Specific requirement and requested material
    if signals.get("specific_requirement"):
        apply("specific_requirement", f'specific requirement ("{_short(signals["specific_requirement"])}")')
    if signals.get("materials"):
        items = ", ".join(label for label, _ in signals["materials"])
        apply("requested_material", f"actively requested material: {items}")

    # Objections / dependencies
    items = fields["Objection_or_Dependency"].meta.get("items", [])
    for label, kind in items:
        if kind == "dependency":
            apply("per_dependency", f"dependency: {label}")
    if any(kind == "mismatch" for _, kind in items):
        apply("budget_mismatch", "budget mismatch / cannot stretch")
    if signals.get("not_interested"):
        apply("not_interested", f'not interested / no follow-up ("{_short(signals["not_interested"])}")')

    # Nothing captured at all
    core = ["Budget", "Timeline", "Purpose", "Projects_Discussed"]
    if all(fields[f].value == NA for f in core):
        apply("no_requirement", "no requirement captured (budget, timeline, purpose, project all missing)")

    probability = max(0, min(100, score))
    if probability >= HOT_THRESHOLD:
        temperature = "Hot"
    elif probability >= WARM_THRESHOLD:
        temperature = "Warm"
    else:
        temperature = "Cold"

    # Override: explicit "not interested / don't follow up / no investment for 12+ months" is always Cold
    long_pause = category == "LONG_DELAY" and months is not None and months >= COLD_OVERRIDE_MONTHS
    if (signals.get("not_interested") or long_pause) and temperature != "Cold":
        temperature = "Cold"
        lines.append("Override → Cold (client said not interested / no investment for 12+ months)")
    elif long_pause:
        lines.append("Cold confirmed: client said no investment for 12+ months")

    if probability != score:
        lines.append(f"Clamped {score} → {probability} (range 0-100)")
    lines.append(f"Final: {probability} → {temperature}")
    return {"probability": probability, "temperature": temperature, "breakdown": lines}
