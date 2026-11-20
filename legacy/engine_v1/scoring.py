"""Buying probability (0-100) and lead temperature - rule based and explainable.

Start at BASE, add/subtract the weights below for every rule that fires,
clamp to 0-100. Every rule that fires is written to Score_Breakdown with the
sentence that triggered it, so any score can be explained line by line.
"""
from __future__ import annotations

from .extractor import NA, parse_budget_text

# ---- All weights in ONE place (easy to explain and tune) -------------------
WEIGHTS = {
    "base": 40,
    "negotiation": 30,             # negotiating / asked for final offer
    "visit_completed_or_confirmed": 25,
    "visit_tentative": 10,
    "timeline_30_days": 20,        # <= 30 days or "this month"
    "timeline_3_months": 10,       # <= 90 days
    "timeline_6_months": 5,        # <= 180 days
    "clear_budget": 5,
    "budget_increased": 5,
    "specific_requirement": 5,     # BHK, number of units, specific project
    "requested_material": 5,       # payment plan, floor plan, inventory, ...
    "per_dependency": -10,         # family decision, sell property, waiting...
    "budget_mismatch": -20,        # cannot stretch
    "far_timeline": -25,           # > 6 months or "not before <far date>"
    "not_interested": -40,         # not interested / do not follow up / no plan to buy
    "no_requirement": -20,         # nothing captured at all
}
HOT_THRESHOLD = 70   # >= 70 Hot
WARM_THRESHOLD = 40  # 40-69 Warm, < 40 Cold
FAR_DAYS = 180       # more than ~6 months away


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

    # Timeline
    timeline = fields["Timeline"]
    days = timeline.meta.get("horizon_days")
    negative = timeline.meta.get("negative", False)
    if timeline.value != NA and days is not None:
        if negative or days > FAR_DAYS:
            if days >= FAR_DAYS:
                apply("far_timeline", f"long delay: {timeline.value}")
        elif days <= 30:
            apply("timeline_30_days", f"timeline within 30 days: {timeline.value}")
        elif days <= 90:
            apply("timeline_3_months", f"timeline within 3 months: {timeline.value}")
        elif days <= FAR_DAYS:
            apply("timeline_6_months", f"timeline within 6 months: {timeline.value}")

    # Budget (only counts when the client said it in a remark, not just a CRM column)
    budget = fields["Budget"]
    if budget.value != NA and budget.source in ("latest", "previous"):
        apply("clear_budget", f"clear budget stated: {budget.value}")
    # Budget increased = latest upper amount is higher than the previous remark (same currency)
    prev = parse_budget_text(lead.get("Previous_Remarks", ""))
    if prev and budget.source == "latest" and prev["currency"] == budget.meta.get("currency") \
            and budget.meta.get("high", 0) > prev["high"]:
        apply("budget_increased", f"budget increased: {prev['value']} → {budget.value}")

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
    long_pause = negative and days is not None and days >= 365
    if (signals.get("not_interested") or long_pause) and temperature != "Cold":
        temperature = "Cold"
        lines.append("Override → Cold (client said not interested / no investment for 12+ months)")
    elif long_pause:
        lines.append("Cold confirmed: client said no investment for 12+ months")

    if probability != score:
        lines.append(f"Clamped {score} → {probability} (range 0-100)")
    lines.append(f"Final: {probability} → {temperature}")
    return {"probability": probability, "temperature": temperature, "breakdown": lines}
