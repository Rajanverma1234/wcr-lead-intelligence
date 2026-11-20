"""Previous-vs-latest conflict detection.

The latest remark always wins, but the change is recorded so nothing is
silently lost, e.g. "Budget changed: ₹4 Cr (previous) → Up to ₹6.5 Cr (latest)".
We run the SAME finder functions separately on previous and latest remarks
(and on the Recorded_* columns) and compare.
"""
from __future__ import annotations

from .extractor import (NA, find_location, find_purpose, find_timeline,
                        lead_sentences, parse_budget_text, _parse_date)


def _budget_key(parsed: dict | None):
    """Comparable form of a budget: (currency, low, high)."""
    return None if not parsed else (parsed["currency"], parsed["low"], parsed["high"])


def _same_ceiling(old: dict, new: dict, new_value: str) -> bool:
    """Not a real conflict: new value is "Up to X" and the old upper amount was also X.

    e.g. L009 "₹7–8 Cr" (previous) and "Up to ₹8 Cr" (latest) agree - the ceiling did not change.
    """
    return (new_value.startswith("Up to") and old["currency"] == new.get("currency")
            and old["high"] == new.get("high"))


def detect_conflicts(lead: dict, fields: dict) -> dict:
    """Return {field_name: [flag, ...]} for every field with a conflict."""
    latest, previous = lead_sentences(lead)
    flags: dict[str, list[str]] = {}

    def add(field_name, text):
        flags.setdefault(field_name, []).append(text)

    # ---- Budget: compare the final value with previous remark and Recorded_Budget
    budget = fields["Budget"]
    if budget.value != NA and budget.source in ("latest", "previous"):
        final_key = _budget_key(budget.meta)
        older = []  # (label, display text, parsed budget)
        if budget.source == "latest":
            prev = parse_budget_text(lead.get("Previous_Remarks", ""))
            if prev:
                older.append(("previous remark", prev["value"], prev))
        recorded = lead.get("Recorded_Budget", "")
        rec = parse_budget_text(recorded) if recorded else None
        if rec:
            older.append(("Recorded_Budget", recorded, rec))
        # Group identical older values so we write one clear flag
        seen = {}
        for label, text, parsed in older:
            key = _budget_key(parsed)
            if key != final_key and not _same_ceiling(parsed, budget.meta, budget.value):
                seen.setdefault(key, [text, []])[1].append(label)
        for text, labels in seen.values():
            add("Budget", f"Budget changed: {text} ({', '.join(labels)}) → {budget.value} ({budget.source})")

    # ---- Timeline / Purpose / Location: previous vs latest
    contact = _parse_date(lead.get("Last_Contact_Date", ""))
    checks = [
        ("Timeline", lambda s: find_timeline(s, contact_date=contact)),
        ("Purpose", find_purpose),
        ("Location_Preference", find_location),
    ]
    for field_name, finder in checks:
        new, old = finder(latest), finder(previous)
        if new and old and new[0] != old[0]:
            add(field_name, f"{field_name.replace('_', ' ')} changed: {old[0]} (previous) → {new[0]} (latest)")
    return flags
