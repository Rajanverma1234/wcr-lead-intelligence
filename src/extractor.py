"""Step 2: rule-based field extraction.

How every field works (same pattern everywhere):
  1. Split Latest_Remarks and Previous_Remarks into sentences.
  2. A `find_<field>(sentences)` function scans sentences with regex rules and
     returns (value, evidence_sentences, meta) or None.
  3. `extract_<field>(lead)` tries LATEST remarks first, then PREVIOUS remarks,
     then (for some fields) a structured column, else "Not Available".
  4. The result is a FieldResult(value, evidence, confidence, source).

Evidence is always an exact sentence copied from the remarks (or a labelled
structured column like "Recorded_Project: M3M BRABUS"), so the validator can
prove it exists in the source row.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dependencies import extract_dependencies
from .negation import is_negated
from .timeline import LATEST as TL_LATEST
from .timeline import NONE as TL_NONE
from .timeline import resolve_timeline

NA = "Not Available"

# Where a value came from (round 2). Client remarks and CRM columns are always kept apart.
LATEST_REMARK, PREVIOUS_REMARK = "LATEST_REMARK", "PREVIOUS_REMARK"
CRM_PROJECT, CRM_BUDGET, CRM_MARKET, CRM_STATUS = (
    "CRM:Recorded_Project", "CRM:Recorded_Budget", "CRM:Market", "CRM:Current_Status")
DERIVED, SRC_NONE = "DERIVED", "NONE"
REMARK_SOURCES = (LATEST_REMARK, PREVIOUS_REMARK)
REMARK_COLUMN = {LATEST_REMARK: "Latest_Remarks", PREVIOUS_REMARK: "Previous_Remarks"}
SOURCE_LABELS = {
    LATEST_REMARK: "Client remark (Latest)", PREVIOUS_REMARK: "Client remark (Previous)",
    CRM_PROJECT: "CRM column (Recorded_Project)", CRM_BUDGET: "CRM column (Recorded_Budget)",
    CRM_MARKET: "CRM column (Market)", CRM_STATUS: "CRM column (Current_Status)",
    DERIVED: "Derived by rules", SRC_NONE: "Not in data",
}

# Field confidence by where the value came from
CONFIDENCE = {
    LATEST_REMARK: 90,     # explicit in latest remarks
    PREVIOUS_REMARK: 75,   # only in previous remarks
    CRM_PROJECT: 60, CRM_BUDGET: 60, CRM_MARKET: 60, CRM_STATUS: 60,  # CRM column only
    DERIVED: 50,
    SRC_NONE: 100,         # nothing in the data -> we are confident it is absent
}


@dataclass
class FieldResult:
    value: str = NA
    evidence: list = field(default_factory=list)  # exact quotes from the row
    confidence: int = CONFIDENCE[SRC_NONE]
    source: str = SRC_NONE     # one of SOURCE_LABELS
    meta: dict = field(default_factory=dict)       # extra data used by scoring
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "value": self.value, "evidence": list(self.evidence),
            "confidence": self.confidence, "source": self.source,
            "source_label": SOURCE_LABELS.get(self.source, self.source),
            "note": self.note,
        }


# ---------------------------------------------------------------- helpers
def split_sentences(text: str) -> list[str]:
    """Split on . ! ? followed by a space. '₹6.5 Cr' is safe (no space after '.')."""
    text = (text or "").strip()
    if not text:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def lead_sentences(lead: dict) -> tuple[list[str], list[str]]:
    return split_sentences(lead.get("Latest_Remarks", "")), split_sentences(lead.get("Previous_Remarks", ""))


def _from_sources(finder, latest, previous, **kwargs) -> FieldResult | None:
    """Latest remarks win; previous remarks are only a fallback."""
    for source, sentences in ((LATEST_REMARK, latest), (PREVIOUS_REMARK, previous)):
        found = finder(sentences, **kwargs)
        if found:
            value, evidence, meta = found
            return FieldResult(value, evidence, CONFIDENCE[source], source, meta)
    return None


MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
MONTH_RE = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
WEEKDAY_RE = r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|tomorrow|today)"


# ================================================================ BUDGET
# ₹ / Rs / INR amounts in Cr or Lakh, with optional range (5–6 or 5-6)
INR_RE = re.compile(
    r"(₹|Rs\.?|INR)\s*(\d+(?:\.\d+)?)(?:\s*(?:–|-|to)\s*(?:₹|Rs\.?|INR)?\s*(\d+(?:\.\d+)?))?\s*(Cr|Crores?|Lakhs?)\b",
    re.IGNORECASE)
# AED amounts in M / K, e.g. AED 1.8M, AED 2M
AED_RE = re.compile(
    r"AED\s*(\d+(?:\.\d+)?)(?:\s*(?:–|-|to)\s*(\d+(?:\.\d+)?))?\s*(M|K|million)?\b", re.IGNORECASE)
UPTO_RE = re.compile(r"(up to|upto|maximum|max\b|can stretch to|not more than)", re.IGNORECASE)
AROUND_RE = re.compile(r"(around|approx|roughly|about)", re.IGNORECASE)


def parse_budget_text(text: str) -> dict | None:
    """Find the first money amount in a piece of text. Never converts currency."""
    best = None
    for regex, kind in ((INR_RE, "INR"), (AED_RE, "AED")):
        m = regex.search(text)
        if m and (best is None or m.start() < best[0].start()):
            best = (m, kind)
    if not best:
        return None
    m, kind = best
    if kind == "INR":
        symbol, low, high, unit = m.group(1), m.group(2), m.group(3), m.group(4)
        symbol = "₹" if symbol == "₹" else symbol.strip()
        unit = "Cr" if unit.lower().startswith("cr") else "Lakh"
        amount = f"{symbol}{low}{'–' + high if high else ''} {unit}"
    else:
        low, high, unit = m.group(1), m.group(2), (m.group(3) or "")
        unit = "M" if unit.lower() in ("m", "million") else unit.upper()
        amount = f"AED {low}{'–' + high if high else ''}{unit}"

    # Qualifier from the words just before the amount ("up to", "around"...)
    before = text[max(0, m.start() - 45):m.start()]
    qualifier = ""
    if UPTO_RE.search(before):
        qualifier = "Up to"
    elif AROUND_RE.search(before) and not high:
        qualifier = "Around"
    value = f"{qualifier} {amount}".strip()
    return {
        "value": value, "currency": kind, "unit": unit, "qualifier": qualifier,
        "low": float(low), "high": float(high or low), "raw": m.group(0),
    }


def find_budget(sentences):
    for s in sentences:
        parsed = parse_budget_text(s)
        if parsed:
            return parsed["value"], [s], parsed
    return None


def extract_budget(lead) -> FieldResult:
    latest, previous = lead_sentences(lead)
    result = _from_sources(find_budget, latest, previous)
    if result:
        return result
    recorded = lead.get("Recorded_Budget", "")
    parsed = parse_budget_text(recorded) if recorded else None
    if parsed:
        return FieldResult(parsed["value"], [f"Recorded_Budget: {recorded}"],
                           CONFIDENCE[CRM_BUDGET], CRM_BUDGET, parsed,
                           note="From Recorded_Budget column only")
    return FieldResult()


# ================================================================ TIMELINE
# Round 2: classification lives in timeline.py (CONFIRMED / TENTATIVE / VAGUE /
# UNCERTAIN / LONG_DELAY / NONE) with latest-overrides-previous.
def extract_timeline(lead) -> FieldResult:
    resolution = resolve_timeline(lead)
    eff = resolution["effective"]
    meta = {"category": eff.category, "months": eff.months_estimate, "raw": eff.raw_text,
            "resolution": resolution}
    if eff.category == TL_NONE:
        return FieldResult(meta=meta)
    source = LATEST_REMARK if eff.source == TL_LATEST else PREVIOUS_REMARK
    return FieldResult(eff.display, [eff.evidence], CONFIDENCE[source], source, meta)


# ================================================================ PURPOSE
PURPOSE_RULES = [("End Use", re.compile(r"\bend[\s-]use\b", re.IGNORECASE)),
                 ("Investment", re.compile(r"\b(invest\w*|rental[\s-]yield|family office)\b", re.IGNORECASE))]


def _first_unnegated(pattern, sentence):
    """First match of pattern in sentence that is NOT inside a negated clause."""
    return next((m for m in pattern.finditer(sentence) if not is_negated(sentence, m.start())), None)


def _negated_hits(field_name, patterns, sentences) -> list[str]:
    """Validation notes for keywords that were ignored because they sit in a negated clause."""
    notes = []
    for s in sentences:
        for pattern in patterns:
            for m in pattern.finditer(s):
                if is_negated(s, m.start()):
                    notes.append(f'{field_name}: "{m.group(0)}" ignored – negated clause ("{s}")')
    return notes


def find_purpose(sentences):
    # "He is NOT looking to invest" / "no investment for 12 months" are negations, not a purpose
    for s in sentences:
        for value, pattern in PURPOSE_RULES:
            if _first_unnegated(pattern, s):
                return value, [s], {}
    return None


def extract_purpose(lead) -> FieldResult:
    latest, previous = lead_sentences(lead)
    result = _from_sources(find_purpose, latest, previous) or FieldResult()
    result.meta["validation_issues"] = _negated_hits("Purpose", [p for _, p in PURPOSE_RULES], latest + previous)
    return result


# ================================================================ PROJECTS
KNOWN_PROJECTS = {
    "M3M BRABUS": r"\b(?:M3M\s+)?BRABUS\b",
    "DLF Privana": r"\b(?:DLF\s+)?Privana\b",
    "Dubai Investment": r"\bDubai Investment\b",
}


def find_projects(sentences):
    names, evidence = [], []
    for s in sentences:
        for name, pattern in KNOWN_PROJECTS.items():
            if _first_unnegated(re.compile(pattern, re.IGNORECASE), s) and name not in names:
                names.append(name)
                if s not in evidence:
                    evidence.append(s)
    return (", ".join(names), evidence, {"names": names}) if names else None


def extract_projects(lead) -> FieldResult:
    """Projects the client named in a remark (latest, else previous); else the CRM column.

    The CRM column is never mixed into a remark value - a difference is flagged instead.
    """
    latest, previous = lead_sentences(lead)
    recorded = lead.get("Recorded_Project", "")
    crm_names = []
    for part in [p.strip() for p in recorded.split("/") if p.strip()]:
        for name, pattern in KNOWN_PROJECTS.items():
            if re.search(pattern, part, re.IGNORECASE) and name not in crm_names:
                crm_names.append(name)

    result = _from_sources(find_projects, latest, previous)
    if result:
        result.meta["crm_names"] = crm_names
        return result
    if crm_names:
        return FieldResult(", ".join(crm_names), [f"Recorded_Project: {recorded}"], CONFIDENCE[CRM_PROJECT],
                           CRM_PROJECT, {"names": crm_names, "crm_names": crm_names},
                           note="From CRM column Recorded_Project only")
    return FieldResult()


# ================================================================ LOCATION
KNOWN_LOCATIONS = ["Golf Course Extension Road", "Dwarka Expressway", "Dubai", "Noida", "Gurugram", "Gurgaon"]
PREFERENCE_CUES = re.compile(
    r"(interested in|prefers?|looking (?:for|at|in)|asked about|decision is now|property in|only)", re.IGNORECASE)
# A property the client OWNS or is SELLING is not a preference ("selling his Noida property")
OWNERSHIP_CUES = re.compile(r"\b(owns?|owned|selling|sell)\b", re.IGNORECASE)


def find_location(sentences):
    for s in sentences:
        if OWNERSHIP_CUES.search(s) or not PREFERENCE_CUES.search(s):
            continue
        found = [loc for loc in KNOWN_LOCATIONS
                 if _first_unnegated(re.compile(r"\b" + re.escape(loc) + r"\b"), s)]
        if found:
            return ", ".join(found), [s], {"locations": found}
    return None


def extract_location(lead) -> FieldResult:
    """Client's location preference - ONLY from remarks. The Market column is a CRM
    field (output column CRM_Market) and is never shown as the client's preference."""
    latest, previous = lead_sentences(lead)
    return _from_sources(find_location, latest, previous) or FieldResult()


# ================================================================ SITE VISIT
VISIT_RULES = [  # checked in this order; first match wins
    ("Completed", r"visit (?:is |was )?completed|visited the site"),
    # Tentative BEFORE Confirmed, so "not confirmed" / "do not mark as confirmed" never become Confirmed
    ("Tentative", r"tentative|may visit|no day is fixed|not (?:yet )?confirmed|not mark the visit as confirmed"
                  r"|has not confirmed|wants to visit|may travel|can visit any"),
    ("Confirmed", r"confirmed"),
    ("Not Now", r"not ready for (?:a )?site visit|do not push for a site visit|don't push for a site visit"),
    ("Not Scheduled", r"not agreed to a site visit|no site visit date|before scheduling visit|before deciding on visit"),
    ("Not Interested", r"not interested"),
]


def find_site_visit(sentences):
    for status, pattern in VISIT_RULES:
        for s in sentences:
            if not re.search(pattern, s, re.IGNORECASE):
                continue
            if status == "Confirmed" and "visit" not in s.lower():
                continue  # "confirmed" must be about a visit
            value, meta = status, {"status": status}
            if status == "Confirmed":
                when = re.search(r"(?:on )?" + WEEKDAY_RE + r"(?: at (\d{1,2}(?::\d{2})?\s*[AP]M))?", s)
                if when:
                    meta["when"] = when.group(0).strip()
                    value = f"Confirmed – {meta['when']}"
            elif status == "Not Now" and "this week" in s.lower():
                value = "Not Now – not this week"
            return value, [s], meta
    return None


def extract_site_visit(lead) -> FieldResult:
    latest, previous = lead_sentences(lead)
    return _from_sources(find_site_visit, latest, previous) or FieldResult()


# ================================================================ OBJECTION / DEPENDENCY
# Round 2: a list with one item per distinct dependency (see dependencies.py).
def extract_objections(lead) -> FieldResult:
    items = extract_dependencies(lead)
    if not items:
        return FieldResult(meta={"items": [], "list": []})
    evidence = list(dict.fromkeys(i["evidence"] for i in items))
    source = items[0]["source"]
    return FieldResult(" | ".join(i["label"] for i in items), evidence, CONFIDENCE[source], source,
                       {"items": [(i["label"], i["kind"]) for i in items], "list": items})


# ================================================================ SIGNALS (used by scoring + messaging)
# Only an EXPLICIT request for material earns points ("asked for the brochure", "send floor plan").
# A casual question ("casually asked the price") is not a request.
MATERIAL_SENTENCE = re.compile(
    r"\b(asked for|asked to (?:whatsapp|send|share|email)|asks for|wants?|send|requested|share|needs?)\b",
    re.IGNORECASE)
CASUAL = re.compile(r"\bcasual(?:ly)?\b|\bjust asked\b|\bonly asked\b", re.IGNORECASE)
MATERIAL_ITEMS = [  # (pattern, label)
    (r"price (?:sheet|list|details)|pricing", "price sheet"),
    (r"\blocation\b", "location"),
    (r"floor plan", "floor plan"),
    (r"payment plan", "payment plan"),
    (r"inventory", "inventory"),
    (r"comparison", "comparison"),
    (r"(\d+) options", "{0} options"),
    (r"expected rental yield", "expected rental yield"),
    (r"rental/resale outlook", "rental/resale outlook"),
    (r"exit/rental case", "exit/rental case"),
    (r"brochure", "brochure"),
    (r"availability", "current availability"),
    (r"available units", "available units"),
    (r"([\w/]+) details", "{0} details"),
]


def find_requested_materials(sentences):
    """Items the client actively asked for, in the order they appear."""
    found = []
    for s in sentences:
        if CASUAL.search(s) or not MATERIAL_SENTENCE.search(s):
            continue
        hits = []
        for pattern, label in MATERIAL_ITEMS:
            for m in re.finditer(pattern, s, re.IGNORECASE):
                if not is_negated(s, m.start()):
                    hits.append((m.start(), label.format(*m.groups()), s))
        for _, label, sent in sorted(hits):
            if label not in [f[0] for f in found]:
                found.append((label, sent))
    return found


def detect_signals(lead) -> dict:
    """Extra yes/no signals with their evidence sentences."""
    latest, previous = lead_sentences(lead)
    all_sents = latest + previous

    def first(pattern, sentences):
        return next((s for s in sentences if re.search(pattern, s, re.IGNORECASE)), None)

    materials = find_requested_materials(latest)
    casual = [s for s in latest if CASUAL.search(s)]
    serious = [s for s in all_sents if not CASUAL.search(s)]  # a casual question is not a requirement
    return {
        "negotiation": first(r"negotiat|final offer", latest),
        "specific_requirement": first(r"\d\s*BHK|\bBHK\b|two units|\d+ units|BRABUS|Privana|ready or near-ready", serious),
        "validation_issues": [f'Requested material / requirement: casual question not counted ("{s}")' for s in casual],
        "materials": materials,
        "not_interested": first(r"not interested|not to follow up|no plan to buy", latest),
        "no_contact": first(r"not to follow up|do not contact|don't contact", latest),
        "callback": first(r"call later|call next week|a call next week|call in " + MONTH_RE + r"|reconnect after", latest),
        "updates": first(r"keep receiving updates", latest),
        "today": first(r"\btoday\b", latest),
    }


def extract_all(lead) -> dict:
    """Run every field extractor for one lead (dict of FieldResult)."""
    return {
        "Budget": extract_budget(lead),
        "Timeline": extract_timeline(lead),
        "Purpose": extract_purpose(lead),
        "Projects_Discussed": extract_projects(lead),
        "Location_Preference": extract_location(lead),
        "Site_Visit_Status": extract_site_visit(lead),
        "Objection_or_Dependency": extract_objections(lead),
    }
