"""Objections and dependencies (round 2): a LIST, one item per distinct dependency.

Each item = {type, label, kind, evidence, source}
  kind: "dependency"     -> -10 each in scoring
        "mismatch"       -> budget mismatch (-20)
        "not_interested" -> not interested / do not follow up
        "info"           -> shown to the sales person, no score impact
  evidence: the exact clause of the remark that triggered the rule.

Two different dependencies in one remark (or one sentence) give two items.
A dependency keyword inside a negated clause ("no home loan needed") is ignored.
"""
from __future__ import annotations

import re

from .negation import is_negated

WHO = r"(wife|husband|spouse|father|mother|parents|family|son|daughter)"
# (type, kind, pattern, label template - {0}, {1} = regex groups)
RULES = [
    # ---- dependencies required by round 2
    ("PROPERTY_SALE", "dependency",
     r"\bsell(?:ing|s)?\s+(?:off\s+)?(?:his|her|their|the|my|our|an?)\s+((?:\w+\s+)?)(?:property|flat|apartment|house|plot)\b",
     "Must sell {0} property first"),
    ("PROPERTY_SALE", "dependency", r"\bproperty bech(?:ne|kar|ke)\b", "Must sell existing property first"),
    ("FAMILY_APPROVAL", "dependency",
     r"\b" + WHO + r"\b[^.;]*?\b(?:approv\w*|final decision|decide|decision|consent)\b", "{0} approval / decision needed"),
    ("FAMILY_APPROVAL", "dependency",
     r"\b(?:discuss\w*|consult\w*|check\w*) with (?:his |her |their |the )?" + WHO + r"\b",
     "{0} approval / decision needed"),
    ("FAMILY_APPROVAL", "dependency", r"\b(ghar wal\w+|biwi|papa|family) se (?:baat|pooch)\w*", "{0} approval / decision needed"),
    ("HOME_LOAN", "dependency", r"\b(?:home loan|housing loan|bank loan|loan approval|loan sanction|bank approval|loan)\b",
     "Home loan / bank approval pending"),
    ("NRI_FUNDS", "dependency",
     r"\b(?:fund transfer|transfer (?:of )?(?:the )?funds|repatriat\w*|remittance)\b",
     "NRI fund transfer / repatriation pending"),
    ("POSSESSION", "dependency",
     r"\b(?:(?:waiting for|after|once|until|till) (?:the )?(?:possession|handover)|"
     r"(?:possession|handover) of (?:his|her|their|another|the other|other|the existing)\b)",
     "Waiting for possession / handover of another property"),
    ("JOB_TRANSFER", "dependency",
     r"\b(?:job (?:confirmation|offer|change|transfer)|(?<!fund )transfer (?:order|confirmation|letter)|"
     r"posting|relocat\w*|joining (?:date|letter))\b",
     "Job / transfer confirmation pending"),
    # ---- round 1 rules (unchanged behaviour)
    ("PAYMENT_PLAN", "dependency", r"payment plan first|if (?:the )?payment plan is suitable",
     "Payment plan needed before deciding"),
    ("DETAILS_BEFORE_VISIT", "dependency", r"before (?:scheduling|deciding on) (?:the |a )?visit",
     "Wants details before committing to a visit"),
    ("FUNDS_TIED_UP", "dependency", r"funds (?:are )?tied up", "Funds tied up in another property"),
    ("BONUS", "dependency", r"after (?:his |her )?annual bonus|waiting for (?:his |her )?bonus", "Waiting for annual bonus"),
    ("FLIGHT", "dependency", r"flight confirmation", "Waiting for flight confirmation"),
    ("NUMBERS", "dependency", r"only if (?:the )?numbers make sense", "Will buy only if the numbers make sense"),
    ("RERA", "info", r"legal/RERA|\bRERA\b", "Needs legal/RERA details"),
    ("BUDGET_MISMATCH", "mismatch", r"cannot stretch|can't stretch|can not stretch",
     "Budget mismatch – client cannot stretch budget"),
    ("COMPARING", "info", r"\bcomparing\b", "Comparing projects before deciding"),
    ("NOT_INTERESTED", "not_interested", r"not interested", "Not interested currently"),
    ("NO_FOLLOW_UP", "not_interested", r"not to follow up", "Asked not to be followed up"),
    ("NO_PLAN", "not_interested", r"no plan to buy", "No plan to buy right now"),
    ("NO_INVESTMENT", "info", r"no investment for", "No investment planned for now"),
    ("NOT_BEFORE_DATE", "info", r"not looking to invest before", "Not investing before the stated date"),
    ("CASUAL_ENQUIRY", "info", r"casually asked", "Only a casual price enquiry"),
    ("VISIT_NOT_AGREED", "info", r"has not agreed to a site visit", "Has not agreed to a site visit yet"),
    ("READY_UNIT", "info", r"ready or near-ready", "Needs a ready or near-ready unit"),
    ("RELATIVE_TRAVEL", "info", r"if (?:he|she) comes from (\w+)", "Relative's visit depends on travel from {0}"),
    ("LIKES_UNIT", "info", r"if (?:he|she) likes the unit", "Decision depends on liking the unit"),
]
COMPILED = [(t, k, re.compile(p, re.IGNORECASE), label) for t, k, p, label in RULES]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]


def _clause(sentence: str, start: int, end: int) -> str:
    """The clause around a match: from the previous , ; to the next , ; . (exact substring)."""
    left = max(sentence.rfind(",", 0, start), sentence.rfind(";", 0, start)) + 1
    right_candidates = [i for i in (sentence.find(",", end), sentence.find(";", end)) if i != -1]
    right = min(right_candidates) if right_candidates else len(sentence)
    return sentence[left:right].strip().rstrip(".")


def find_items(text: str, source: str) -> list[dict]:
    items, seen_types = [], set()
    for sentence in _sentences(text):
        for dtype, kind, pattern, template in COMPILED:
            if dtype in seen_types:
                continue
            m = pattern.search(sentence)
            if not m:
                continue
            if kind == "dependency" and is_negated(sentence, m.start()):
                continue
            groups = [g.strip() for g in m.groups()] if m.groups() else []
            label = template.format(*groups) if groups else template
            label = label[0].upper() + label[1:]
            label = re.sub(r"\s+", " ", label)
            items.append({"type": dtype, "kind": kind, "label": label,
                          "evidence": _clause(sentence, m.start(), m.end()), "source": source})
            seen_types.add(dtype)
    return items


def extract_dependencies(lead: dict) -> list[dict]:
    """Latest remark first; the previous remark is used only if the latest has none."""
    latest = find_items(lead.get("Latest_Remarks", ""), "LATEST_REMARK")
    return latest or find_items(lead.get("Previous_Remarks", ""), "PREVIOUS_REMARK")
