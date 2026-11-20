"""Anti-hallucination checks.

Rule: nothing reaches the output unless it can be traced back to the row.
  1. Every evidence quote must be a (case-insensitive) substring of the row.
  2. Every number inside an extracted value must appear in its evidence
     (so "₹6.5 Cr" is only allowed if "6.5" is in the quoted sentence).
  3. Every number / currency / month / weekday / project / location inside a
     WhatsApp message must appear in the row.
If a check fails, the value becomes "Not Available" (or the message falls
back to a safe generic template).
"""
from __future__ import annotations

import re

from .extractor import KNOWN_LOCATIONS, KNOWN_PROJECTS, NA, SRC_NONE, FieldResult

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
MONTH_WORDS = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# Words that are only allowed in a message if the row contains them
FACT_WORDS = MONTH_WORDS + WEEKDAYS + ["BRABUS", "Privana", "M3M", "DLF", "Diwali", "BHK"] + KNOWN_LOCATIONS


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def evidence_in_source(evidence: str, source_text: str) -> bool:
    """Case-insensitive substring check (whitespace-normalised)."""
    return bool(evidence) and _norm(evidence) in _norm(source_text)


def validate_field(result: FieldResult, source_text: str) -> FieldResult:
    """Return the field unchanged if it is grounded, else 'Not Available'."""
    if result.value == NA:
        return result
    problems = []
    if not result.evidence:
        problems.append("no evidence")
    for quote in result.evidence:
        if not evidence_in_source(quote, source_text):
            problems.append(f"evidence not found in row: {quote!r}")
    joined_evidence = " ".join(result.evidence)
    value_for_numbers = result.value
    for name in KNOWN_PROJECTS:  # the "3" in "M3M" is part of a name, not a fact
        value_for_numbers = value_for_numbers.replace(name, "")
    for number in NUMBER_RE.findall(value_for_numbers):
        if number not in joined_evidence:
            problems.append(f"number {number} not in evidence")
    if problems:
        return FieldResult(NA, [], 40, SRC_NONE,
                           note="Rejected by validator: " + "; ".join(problems))
    return result


def unsupported_facts(message: str, source_text: str) -> list[str]:
    """List every fact-like token in a message that is NOT in the source row."""
    src = _norm(source_text)
    bad = []
    for number in NUMBER_RE.findall(message):
        if number not in src:
            bad.append(number)
    for word in FACT_WORDS:
        # case-sensitive on purpose: the month "May" vs the verb "may"
        if re.search(r"\b" + re.escape(word) + r"\b", message) and word.lower() not in src:
            bad.append(word)
    for name in KNOWN_PROJECTS:
        if name.lower() in message.lower() and name.lower() not in src:
            bad.append(name)
    if "₹" in message and "₹" not in source_text:
        bad.append("₹")
    if "AED" in message and "AED" not in source_text:
        bad.append("AED")
    return bad


def validate_message(message: str, source_text: str) -> bool:
    return not unsupported_facts(message, source_text)
