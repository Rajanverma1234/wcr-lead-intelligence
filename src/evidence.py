"""Evidence and source validation (round 2) - runs AFTER extraction.

validate_evidence(lead, fields) -> (validated_fields, issues)

Every field is an object {value, source, evidence, confidence}. A field keeps its
value only if ALL checks pass, otherwise it becomes exactly "Not Available" with
source NONE and the reason is logged in Validation_Issues:

  a) Remark source  -> every evidence quote must be an exact (case/whitespace-
     normalised) substring of THAT remark (latest or previous - not any text in the row).
     CRM source     -> evidence must be "<Column>: <cell value>" for that exact column.
  b) Every number, project name, location, month and weekday inside the value must
     appear in its evidence text (or in the stated CRM column).
  c) Location_Preference may only come from a client remark - never from a CRM column.
"""
from __future__ import annotations

import re

from .extractor import (CRM_BUDGET, CRM_MARKET, CRM_PROJECT, CRM_STATUS, DERIVED, KNOWN_LOCATIONS,
                        KNOWN_PROJECTS, NA, REMARK_COLUMN, REMARK_SOURCES, SOURCE_LABELS, SRC_NONE,
                        FieldResult)
from .timeline import TimelineResult

CRM_COLUMN = {CRM_PROJECT: "Recorded_Project", CRM_BUDGET: "Recorded_Budget",
              CRM_MARKET: "Market", CRM_STATUS: "Current_Status"}
REJECTED_CONFIDENCE = 40
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
MONTH_WORDS = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
REMARK_ONLY_FIELDS = {"Location_Preference"}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def facts_in_value(value: str) -> list[str]:
    """Numbers, project names, locations, months and weekdays inside a value."""
    facts, rest = [], value
    for name, pattern in KNOWN_PROJECTS.items():
        if re.search(pattern, rest, re.IGNORECASE):
            facts.append(("project", name, pattern))
            rest = re.sub(pattern, " ", rest, flags=re.IGNORECASE)  # "3" in "M3M" is not a number
    for loc in KNOWN_LOCATIONS:
        if re.search(r"\b" + re.escape(loc) + r"\b", rest, re.IGNORECASE):
            facts.append(("location", loc, r"\b" + re.escape(loc) + r"\b"))
    for word in MONTH_WORDS + WEEKDAYS:
        if re.search(r"\b" + word + r"\b", rest):  # case-sensitive: month "May" vs verb "may"
            facts.append(("date word", word, r"\b" + word + r"\b"))
    for number in NUMBER_RE.findall(rest):
        facts.append(("number", number, re.escape(number)))
    return facts


def check_field(lead: dict, name: str, f: FieldResult) -> list[str]:
    """Return a list of problems (empty = valid)."""
    problems = []
    if not f.evidence:
        return [f"{name}: value without evidence"]
    if name in REMARK_ONLY_FIELDS and f.source not in REMARK_SOURCES:
        return [f"{name}: only a client remark may set this field, got {SOURCE_LABELS.get(f.source, f.source)}"]

    if f.source in REMARK_SOURCES:
        remark = lead.get(REMARK_COLUMN[f.source], "")
        for quote in f.evidence:
            if norm(quote) not in norm(remark):
                problems.append(f'{name}: evidence not found in {REMARK_COLUMN[f.source]}: "{quote}"')
        allowed_text = " ".join(f.evidence)
    elif f.source in CRM_COLUMN:
        column = CRM_COLUMN[f.source]
        cell = lead.get(column, "")
        expected = f"{column}: {cell}"
        if not cell or any(norm(q) != norm(expected) for q in f.evidence):
            problems.append(f"{name}: evidence does not match CRM column {column}")
        allowed_text = cell
    elif f.source == DERIVED:
        allowed_text = " ".join(f.evidence)
    else:
        return [f"{name}: unknown source {f.source!r}"]

    for kind, fact, pattern in facts_in_value(f.value):
        if not re.search(pattern, allowed_text, re.IGNORECASE if kind != "date word" else 0):
            problems.append(f'{name}: {kind} "{fact}" in value is not in its evidence')
    return problems


def rejected(name: str, reason: str) -> FieldResult:
    meta = {}
    if name == "Timeline":  # scoring needs a timeline object - give it NONE
        none = TimelineResult(source=SRC_NONE)
        meta = {"category": "NONE", "months": None, "raw": "",
                "resolution": {"effective": none, "latest": none, "previous": none,
                               "history": "", "conflict_flag": None, "ignored_previous": None}}
    if name == "Objection_or_Dependency":
        meta = {"items": [], "list": []}
    return FieldResult(NA, [], REJECTED_CONFIDENCE, SRC_NONE, meta, note="Rejected by validator: " + reason)


def validate_evidence(lead: dict, fields: dict) -> tuple[dict, list[str]]:
    validated, issues = {}, []
    for name, f in fields.items():
        issues.extend(f.meta.get("validation_issues", []))  # e.g. negated keyword ignored by the extractor
        if f.value == NA:
            validated[name] = f
            continue
        problems = check_field(lead, name, f)
        if problems:
            issues.extend(f"{p} → set to Not Available" for p in problems)
            validated[name] = rejected(name, "; ".join(problems))
        else:
            validated[name] = f
    return validated, issues
