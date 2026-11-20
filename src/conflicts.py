"""Previous-vs-latest (and CRM-vs-remark) conflicts as TYPED flags.

Field-level merge: for every field the latest remark wins if it has a value;
otherwise the previous value is kept (source PREVIOUS_REMARK). A previous value
that the latest remark contradicts is never silently dropped - it becomes a flag:

  TIMELINE_CHANGED, BUDGET_CHANGED, BUDGET_INCREASED, CURRENCY_DIFFERENT,
  PURPOSE_CHANGED, PROJECT_CHANGED, LOCATION_CHANGED, SITE_VISIT_CHANGED,
  CRM_VS_REMARK_MISMATCH, DUPLICATE_PHONE (added in pipeline.py)

Budgets are compared as RANGES in rupees (Cr / Lakh). A change is a conflict only
when the ranges do NOT overlap, so "₹7–8 Cr" vs "up to ₹8 Cr" is not a conflict.
AED is converted only if AED_TO_INR is configured; otherwise a different currency
is flagged as CURRENCY_DIFFERENT and never converted.
"""
from __future__ import annotations

from .extractor import (NA, find_budget, find_location, find_projects, find_purpose,
                        find_site_visit, lead_sentences, parse_budget_text)

AED_TO_INR: float | None = None      # set a rate here to allow AED <-> INR comparison
AROUND_TOLERANCE = 0.10              # "around ₹5 Cr" = ₹4.5–5.5 Cr
UNIT_FACTOR = {"Cr": 1e7, "Lakh": 1e5, "M": 1e6, "K": 1e3, "": 1.0}


def budget_range(parsed: dict) -> tuple[str, float, float]:
    """(currency, low, high) in base units. 'Up to X' is compared by its ceiling X."""
    factor = UNIT_FACTOR.get(parsed.get("unit", ""), 1.0)
    low, high = parsed["low"] * factor, parsed["high"] * factor
    currency = parsed["currency"]
    if currency == "AED" and AED_TO_INR:
        low, high, currency = low * AED_TO_INR, high * AED_TO_INR, "INR"
    qualifier = parsed.get("qualifier", "")
    if qualifier == "Up to":
        low = high
    elif qualifier == "Around":
        low, high = low * (1 - AROUND_TOLERANCE), high * (1 + AROUND_TOLERANCE)
    return currency, low, high


def compare_budgets(old: dict | None, new: dict | None) -> str:
    """SAME (overlapping) / INCREASED / CHANGED / CURRENCY_DIFFERENT / MISSING."""
    if not old or not new:
        return "MISSING"
    c1, lo1, hi1 = budget_range(old)
    c2, lo2, hi2 = budget_range(new)
    if c1 != c2:
        return "CURRENCY_DIFFERENT"
    if lo1 <= hi2 and lo2 <= hi1:
        return "SAME"
    return "INCREASED" if hi2 > hi1 else "CHANGED"


def remark_budgets(lead: dict) -> tuple[dict | None, dict | None]:
    """(previous-remark budget, latest-remark budget) parsed separately."""
    latest, previous = lead_sentences(lead)
    new, old = find_budget(latest), find_budget(previous)
    return (old[2] if old else None), (new[2] if new else None)


def detect_conflicts(lead: dict, fields: dict) -> dict:
    """Return {field_name: [typed flag, ...]} for every field with a conflict."""
    latest, previous = lead_sentences(lead)
    flags: dict[str, list[str]] = {}

    def add(field_name, text):
        flags.setdefault(field_name, []).append(text)

    # ---- Timeline (classified per remark in timeline.py)
    timeline_flag = fields["Timeline"].meta.get("resolution", {}).get("conflict_flag")
    if timeline_flag:
        add("Timeline", timeline_flag)

    # ---- Budget: previous remark vs latest remark
    old, new = remark_budgets(lead)
    result = compare_budgets(old, new)
    if result in ("INCREASED", "CHANGED"):
        add("Budget", f'BUDGET_{result}: "{old["value"]}" (previous remark) → "{new["value"]}" (latest remark)')
    elif result == "CURRENCY_DIFFERENT":
        add("Budget", f'CURRENCY_DIFFERENT: "{old["value"]}" (previous remark) vs "{new["value"]}" (latest remark)'
                      " – not converted")

    # ---- Budget: CRM column vs the budget the client stated in a remark
    budget = fields["Budget"]
    recorded = lead.get("Recorded_Budget", "")
    crm = parse_budget_text(recorded) if recorded else None
    if budget.value != NA and budget.source in ("latest", "previous") and crm:
        where = "latest remark" if budget.source == "latest" else "previous remark"
        crm_result = compare_budgets(crm, budget.meta)
        if crm_result == "CURRENCY_DIFFERENT":
            add("Budget", f'CURRENCY_DIFFERENT: CRM Recorded_Budget "{recorded}" vs "{budget.value}" ({where})'
                          " – not converted")
        elif crm_result in ("INCREASED", "CHANGED"):
            add("Budget", f'CRM_VS_REMARK_MISMATCH: CRM Recorded_Budget "{recorded}" vs "{budget.value}" ({where})')

    # ---- Projects: CRM column vs remark
    projects = fields["Projects_Discussed"]
    crm_projects = projects.meta.get("crm_names", [])
    remark_projects = projects.meta.get("names", []) if projects.source in ("latest", "previous") else []
    missing = [p for p in crm_projects if p not in remark_projects]
    if remark_projects and missing:
        add("Projects_Discussed", f'CRM_VS_REMARK_MISMATCH: CRM Recorded_Project "{lead.get("Recorded_Project", "")}"'
                                  f' vs remark "{", ".join(remark_projects)}"')

    # ---- Purpose / Projects / Location / Site visit: previous vs latest
    checks = [
        ("Purpose", "PURPOSE_CHANGED", find_purpose),
        ("Projects_Discussed", "PROJECT_CHANGED", find_projects),
        ("Location_Preference", "LOCATION_CHANGED", find_location),
        ("Site_Visit_Status", "SITE_VISIT_CHANGED", find_site_visit),
    ]
    for field_name, flag_type, finder in checks:
        new_v, old_v = finder(latest), finder(previous)
        if not (new_v and old_v) or new_v[0] == old_v[0]:
            continue
        if flag_type == "PROJECT_CHANGED" and set(old_v[2]["names"]) <= set(new_v[2]["names"]):
            continue  # latest only adds projects - nothing contradicted
        add(field_name, f'{flag_type}: "{old_v[0]}" (previous remark) → "{new_v[0]}" (latest remark)')
    return flags
