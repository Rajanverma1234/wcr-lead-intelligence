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
from datetime import date

NA = "Not Available"

# Field confidence by where the value came from
CONFIDENCE = {
    "latest": 90,      # explicit in latest remarks
    "previous": 75,    # only in previous remarks
    "structured": 60,  # only in a structured column (Recorded_*)
    "inferred": 50,    # inferred / fallback (e.g. Market column)
    "absent": 100,     # nothing in the data -> we are confident it is absent
}


@dataclass
class FieldResult:
    value: str = NA
    evidence: list = field(default_factory=list)  # exact quotes from the row
    confidence: int = CONFIDENCE["absent"]
    source: str = "absent"     # latest / previous / structured / inferred / absent
    meta: dict = field(default_factory=dict)       # extra data used by scoring
    note: str = ""             # e.g. "Market column fallback"

    def as_dict(self) -> dict:
        return {
            "value": self.value, "evidence": list(self.evidence),
            "confidence": self.confidence, "source": self.source,
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
    for source, sentences in (("latest", latest), ("previous", previous)):
        found = finder(sentences, **kwargs)
        if found:
            value, evidence, meta = found
            return FieldResult(value, evidence, CONFIDENCE[source], source, meta)
    return None


MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
MONTH_RE = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
WEEKDAY_RE = r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|tomorrow|today)"


def _parse_date(text: str) -> date | None:
    try:
        y, m, d = (int(x) for x in text[:10].split("-"))
        return date(y, m, d)
    except Exception:
        return None


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
        "value": value, "currency": kind, "unit": unit,
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
                           CONFIDENCE["structured"], "structured", parsed,
                           note="From Recorded_Budget column only")
    return FieldResult()


# ================================================================ TIMELINE
# Sentences about visits/travel describe a SITE VISIT, not a buying timeline.
VISIT_WORDS = re.compile(r"\b(visit|travel)", re.IGNORECASE)
TENTATIVE_WORDS = re.compile(r"\b(maybe|probably|may|might|if)\b", re.IGNORECASE)


def find_timeline(sentences, contact_date: date | None = None):
    """Return (text as stated, evidence, meta). meta.horizon_days is only set
    when it is clearly computable; meta.negative marks 'not before / no plan'.
    """
    full_text = " ".join(sentences)
    for s in sentences:
        low = s.lower()
        if VISIT_WORDS.search(s):
            continue

        # 1. No plan to buy at all
        if re.search(r"no plan to buy", low):
            return "No plan to buy right now", [s], {"horizon_days": None, "negative": True}

        # 2. "not ... for (at least / the next) N months"
        m = re.search(r"\b(?:no|not)\b[^.]*?\bfor (?:at least |the next )?(\d+)\s*(months?|years?)", low)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            days = n * (365 if unit.startswith("year") else 30)
            if "follow up" in low:
                text = f"No follow-up for the next {n} {unit} (client request)"
            else:
                text = f"No purchase for at least {n} {unit}"
            return text, [s], {"horizon_days": days, "negative": True, "months": n}

        # 3. A month + year written in the text, e.g. "December 2027"
        m = re.search(MONTH_RE + r"\s+(\d{4})", s)
        if m:
            month, year = m.group(1), int(m.group(2))
            evidence = [s]
            negative = "before" in low
            # "Asked us to call in January 2027 ... No purchase before that."
            follow = re.search(r"no purchase before that[^.]*\.", full_text, re.IGNORECASE)
            if not negative and follow:
                negative = True
                evidence.append(follow.group(0))
            text = f"Not before {month} {year}" if negative else f"{month} {year}"
            horizon = None
            if contact_date:
                horizon = (date(year, MONTHS.index(month.lower()) + 1, 1) - contact_date).days
            return text, evidence, {"horizon_days": horizon, "negative": negative}

        # 4. "within N days/weeks/months" or "within 3–4 months"
        m = re.search(r"within (\d+)\s*(?:(?:–|-|to)\s*(\d+))?\s*(days?|weeks?|months?)", low)
        if m:
            a, b, unit = m.group(1), m.group(2), m.group(3)
            per = {"d": 1, "w": 7, "m": 30}[unit[0]]
            days = int(b or a) * per
            text = f"Within {a}{'–' + b if b else ''} {unit}"
            if TENTATIVE_WORDS.search(s):
                text += " (tentative)"
            return text, [s], {"horizon_days": days, "negative": False, "phrase": m.group(0)}

        # 5. "after Diwali" (+ optional "roughly 3 months from now")
        if "after diwali" in low:
            m = re.search(r"(\d+) months from now", low)
            text = f"After Diwali (roughly {m.group(1)} months)" if m else "After Diwali"
            days = int(m.group(1)) * 30 if m else None
            return text, [s], {"horizon_days": days, "negative": False, "phrase": "after Diwali"}

        # 6. "N months from now"
        m = re.search(r"(\d+) months from now", low)
        if m:
            return f"About {m.group(1)} months from now", [s], {"horizon_days": int(m.group(1)) * 30, "negative": False}

        # 7. Short relative periods
        for phrase, text, days in (("this month", "This month", 30), ("this quarter", "This quarter", 90),
                                   ("next month", "Next month", 60), ("this week", "This week", 7)):
            if phrase in low:
                if TENTATIVE_WORDS.search(s):
                    text += " (tentative)"
                return text, [s], {"horizon_days": days, "negative": False, "phrase": phrase}

        # 8. Vague "next year" -> never a fake date
        if "next year" in low:
            return "Sometime next year (no month given)", [s], {"horizon_days": None, "negative": False, "vague": True}
    return None


def extract_timeline(lead) -> FieldResult:
    latest, previous = lead_sentences(lead)
    contact = _parse_date(lead.get("Last_Contact_Date", ""))
    return _from_sources(find_timeline, latest, previous, contact_date=contact) or FieldResult()


# ================================================================ PURPOSE
def find_purpose(sentences):
    for s in sentences:
        low = s.lower()
        if re.search(r"end[\s-]use", low):
            return "End Use", [s], {}
        # "no investment for 12 months" is a negation, not a purpose statement
        if re.search(r"\b(invest\w*|rental[\s-]yield|family office)\b", low) and not re.search(r"\bno investment\b", low):
            return "Investment", [s], {}
    return None


def extract_purpose(lead) -> FieldResult:
    latest, previous = lead_sentences(lead)
    return _from_sources(find_purpose, latest, previous) or FieldResult()


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
            if re.search(pattern, s, re.IGNORECASE) and name not in names:
                names.append(name)
                if s not in evidence:
                    evidence.append(s)
    return (", ".join(names), evidence, {"names": names}) if names else None


def extract_projects(lead) -> FieldResult:
    latest, previous = lead_sentences(lead)
    result = _from_sources(find_projects, latest, previous)
    names = list(result.meta["names"]) if result else []
    evidence = list(result.evidence) if result else []

    # Add projects from the Recorded_Project column (split "A / B"), deduplicated
    recorded = lead.get("Recorded_Project", "")
    added_from_column = False
    for part in [p.strip() for p in recorded.split("/") if p.strip()]:
        for name, pattern in KNOWN_PROJECTS.items():
            if re.search(pattern, part, re.IGNORECASE) and name not in names:
                names.append(name)
                added_from_column = True
    if added_from_column:
        evidence.append(f"Recorded_Project: {recorded}")

    if not names:
        return FieldResult()
    if result:
        return FieldResult(", ".join(names), evidence, result.confidence, result.source, {"names": names})
    return FieldResult(", ".join(names), evidence, CONFIDENCE["structured"], "structured",
                       {"names": names}, note="From Recorded_Project column only")


# ================================================================ LOCATION
KNOWN_LOCATIONS = ["Golf Course Extension Road", "Dwarka Expressway", "Dubai", "Noida", "Gurugram"]
PREFERENCE_CUES = re.compile(
    r"(interested in|prefers?|looking (?:for|at|in)|asked about|decision is now|property in|only)", re.IGNORECASE)
# A property the client OWNS or is SELLING is not a preference (L001 Noida)
OWNERSHIP_CUES = re.compile(r"\b(owns?|owned|selling|sell)\b", re.IGNORECASE)


def find_location(sentences):
    for s in sentences:
        if OWNERSHIP_CUES.search(s) or not PREFERENCE_CUES.search(s):
            continue
        found = [loc for loc in KNOWN_LOCATIONS if re.search(r"\b" + re.escape(loc) + r"\b", s)]
        if found:
            return ", ".join(found), [s], {"locations": found}
    return None


def extract_location(lead) -> FieldResult:
    latest, previous = lead_sentences(lead)
    result = _from_sources(find_location, latest, previous)
    if result:
        return result
    market = lead.get("Market", "")
    if market:  # fallback: the Market column, clearly labelled, lower confidence
        return FieldResult(f"{market} (from Market column)", [f"Market: {market}"],
                           CONFIDENCE["inferred"], "inferred", {"locations": [market]},
                           note="Not stated by client; Market column used as fallback")
    return FieldResult()


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
# kind: "dependency" (-10 each), "mismatch" (-20), "not_interested" (-40), "info" (no score impact)
OBJECTION_RULES = [
    (r"\bsell(?:ing)? (?:his|her|their|the) (\w+) property", "Must sell {0} property first", "dependency"),
    (r"discussing with (?:her |his |the )?family", "Family decision pending", "dependency"),
    (r"wife will take the final decision", "Wife will take the final decision", "dependency"),
    (r"payment plan first|if (?:the )?payment plan is suitable", "Payment plan needed before deciding", "dependency"),
    (r"before (?:scheduling|deciding on) (?:the |a )?visit", "Wants details before committing to a visit", "dependency"),
    (r"legal/RERA|\bRERA\b", "Needs legal/RERA details", "info"),
    (r"cannot stretch|can't stretch|can not stretch", "Budget mismatch – client cannot stretch budget", "mismatch"),
    (r"funds (?:are )?tied up", "Funds tied up in another property", "dependency"),
    (r"after (?:his |her )?annual bonus|waiting for (?:his |her )?bonus", "Waiting for annual bonus", "dependency"),
    (r"flight confirmation", "Waiting for flight confirmation", "dependency"),
    (r"only if (?:the )?numbers make sense", "Will buy only if the numbers make sense", "dependency"),
    (r"\bcomparing\b", "Comparing projects before deciding", "info"),
    (r"not interested", "Not interested currently", "not_interested"),
    (r"not to follow up", "Asked not to be followed up", "not_interested"),
    (r"no plan to buy", "No plan to buy right now", "not_interested"),
    (r"no investment for", "No investment planned for now", "info"),
    (r"not looking to invest before", "Not investing before the stated date", "info"),
    (r"casually asked", "Only a casual price enquiry", "info"),
    (r"has not agreed to a site visit", "Has not agreed to a site visit yet", "info"),
    (r"ready or near-ready", "Needs a ready or near-ready unit", "info"),
    (r"if (?:he|she) comes from (\w+)", "Relative's visit depends on travel from {0}", "info"),
    (r"if (?:he|she) likes the unit", "Decision depends on liking the unit", "info"),
]


def find_objections(sentences):
    labels, evidence, kinds = [], [], []
    for s in sentences:
        for pattern, template, kind in OBJECTION_RULES:
            m = re.search(pattern, s, re.IGNORECASE)
            if m:
                label = template.format(*m.groups())
                if label not in labels:
                    labels.append(label)
                    kinds.append(kind)
                    if s not in evidence:
                        evidence.append(s)
    if not labels:
        return None
    return "; ".join(labels), evidence, {"items": list(zip(labels, kinds))}


def extract_objections(lead) -> FieldResult:
    latest, previous = lead_sentences(lead)
    return _from_sources(find_objections, latest, previous) or FieldResult(meta={"items": []})


# ================================================================ SIGNALS (used by scoring + messaging)
MATERIAL_SENTENCE = re.compile(r"\b(asked|wants?|send|requested|share)\b", re.IGNORECASE)
MATERIAL_ITEMS = [  # (pattern, label)
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
        if not MATERIAL_SENTENCE.search(s):
            continue
        hits = []
        for pattern, label in MATERIAL_ITEMS:
            for m in re.finditer(pattern, s, re.IGNORECASE):
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
    return {
        "negotiation": first(r"negotiat|final offer", latest),
        "specific_requirement": first(r"\d\s*BHK|\bBHK\b|two units|\d+ units|BRABUS|Privana|ready or near-ready", all_sents),
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
