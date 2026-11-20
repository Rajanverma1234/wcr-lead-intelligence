"""Timeline classifier (round 2).

classify_remark(text) -> TimelineResult(category, raw_text, months_estimate, source)

Categories
  CONFIRMED  - firm window ("within 2 months", "this month", "by March 2027")
  TENTATIVE  - hedged window ("probably in 2-3 months", "maybe within 3-4 months", "... if ...")
  VAGUE      - no usable window ("sometime next year", "in future", "later", "agle saal")
  UNCERTAIN  - explicitly unknown / on hold ("timeline is uncertain", "not sure when", "pakka nahi")
  LONG_DELAY - more than 6 months away ("after 1 year", "no investment for 12 months", "postponed to 2028")
  NONE       - no timeline mentioned

Rules
  * Inside one remark the most cautious statement wins:
        UNCERTAIN > LONG_DELAY > VAGUE > CONFIRMED / TENTATIVE
    so "uncertain" always beats a number in the same remark.
  * Clauses about a site visit, travel or a call-back are NOT a purchase timeline
    ("call next week", "visit on Sunday") and are skipped.
  * raw_text is always an exact substring of the remark.
  * months_estimate is only used for scoring. It is never shown as a date and no
    absolute date is ever computed that is not already written in the remark.

resolve_timeline(lead) applies LATEST-OVERRIDES-PREVIOUS (see its docstring).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

CONFIRMED, TENTATIVE, VAGUE, UNCERTAIN, LONG_DELAY, NONE = (
    "CONFIRMED", "TENTATIVE", "VAGUE", "UNCERTAIN", "LONG_DELAY", "NONE")
LATEST, PREVIOUS = "LATEST_REMARK", "PREVIOUS_REMARK"
LONG_DELAY_MONTHS = 6
# Lower number = more cautious = wins when one remark contains several statements
PRIORITY = {UNCERTAIN: 0, LONG_DELAY: 1, VAGUE: 2, CONFIRMED: 3, TENTATIVE: 3}
LABELS = {CONFIRMED: "Confirmed", TENTATIVE: "Tentative", VAGUE: "Vague",
          UNCERTAIN: "Uncertain", LONG_DELAY: "Long delay"}

MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
MONTH_YEAR = r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})"
NUM = r"(\d+(?:\.\d+)?)(?:\s*(?:–|-|to|se)\s*(\d+(?:\.\d+)?))?"
UNIT_MONTHS = {"day": 1 / 30, "din": 1 / 30, "week": 0.25, "hafte": 0.25, "hafta": 0.25,
               "month": 1, "mahine": 1, "mahina": 1, "year": 12, "saal": 12}

# ---- patterns (all matched on the original text with IGNORECASE) -------------
UNCERTAIN_ALWAYS = re.compile(
    r"(?:\b(?:purchase |buying |investment )?timeline (?:is )?(?:currently |still )?)?"
    r"\b(?:on hold|undecided|not decided|will decide later|decide later|no plan to buy|pakka nahi|"
    r"abhi decide nahi|tay nahi)\b", re.IGNORECASE)
# Generic doubt words count only when the clause is about timing/buying
# ("not sure about location yet" is NOT a timeline statement).
UNCERTAIN_GENERIC = re.compile(
    r"(?:\b(?:purchase |buying |investment )?timeline (?:is )?(?:currently |still )?)?"
    r"\b(?:uncertain|unclear|not sure|unsure|don't know|do not know|pata nahi)\b", re.IGNORECASE)
OTHER_SUBJECT = re.compile(r"\b(location|budget|project|unit|size|bhk|area|configuration|tower|floor)\b", re.IGNORECASE)
TIMING_CUE = re.compile(r"\b(timeline|timing|when|date|purchase|buy|buying|invest\w*|clos\w+|kab)\b", re.IGNORECASE)

LONG_DELAY_PHRASE = re.compile(
    r"\b(?:next to next year|year after next|agle se agle saal|"
    r"postponed (?:to|till|until|by|for) [^.,;]*?(?:\d{4}|years?)|"
    r"after (?:a|one|\d+(?:\.\d+)?) years?|\d+ saal baad|ek saal baad)\b", re.IGNORECASE)
# "not ... for (at least / the next) N months" - a pause the client asked for
NEGATIVE_PAUSE = re.compile(
    r"\b(?:no|not)\b[^.,;]*?\bfor (?:at least |the next |next )?(\d+)\s*(months?|years?)", re.IGNORECASE)

VAGUE_PHRASE = re.compile(
    r"\b(?:(?:sometime |some time )?next year|in (?:the )?future|eventually|later|after some time|some ?day|"
    r"agle saal|baad mein|kabhi bhi|not anytime soon)\b", re.IGNORECASE)

WINDOW_PATTERNS = [
    # within / in / next N(-M) days|weeks|months|years   (+ optional hedge just before)
    re.compile(r"(?:\b(?:maybe|probably|around|about|roughly|approx\.?|approximately|likely)\s+)?"
               r"\b(?:within|in the next|over the next|in|next)\s+" + NUM + r"\s*(day|week|month|year)s?\b",
               re.IGNORECASE),
    # N(-M) months from now
    re.compile(r"(?:\b(?:around|about|roughly|approx\.?|approximately)\s+)?\b" + NUM
               + r"\s*(day|week|month|year)s? from now\b", re.IGNORECASE),
    # Hinglish: "2 mahine mein", "3 hafte ke andar"
    re.compile(r"\b" + NUM + r"\s*(mahine|mahina|hafte|hafta|din|saal)\s*(?:mein|me|main|ke andar)\b", re.IGNORECASE),
    # "around 2-3 months" (hedged, no preposition)
    re.compile(r"\b(?:around|about|roughly|approx\.?|approximately)\s+" + NUM + r"\s*(day|week|month|year)s?\b",
               re.IGNORECASE),
]
RELATIVE = [  # phrase -> months
    (r"this week|is hafte", 0.25), (r"next week|agle hafte", 0.5), (r"this month|is mahine", 1),
    (r"next month|agle mahine", 2), (r"this quarter", 3), (r"next quarter", 6),
]
RELATIVE_RE = re.compile(r"\b(" + "|".join(p for p, _ in RELATIVE) + r")\b", re.IGNORECASE)
MONTH_YEAR_RE = re.compile(r"(?:\bnot\b[^.,;]*?)?\b(?:(?:before|by|in|till|until|after)\s+)?" + MONTH_YEAR,
                           re.IGNORECASE)
NO_PURCHASE_BEFORE_THAT = re.compile(r"\bno purchase before that\b|\bnot before that\b", re.IGNORECASE)

# Hedge words anywhere in the sentence make a window TENTATIVE
HEDGE = re.compile(
    r"\b(maybe|probably|planning to|plans? to|likely|might|may(?!\s+\d{4})|perhaps|possibly|tentative\w*|"
    r"if|subject to|hopefully|considering|thinking of|shayad|lagbhag|ho sakta)\b", re.IGNORECASE)
# Clauses about a visit / travel / call-back are not a purchase timeline
NOT_PURCHASE_CLAUSE = re.compile(r"\b(visit\w*|travel\w*|site|call\w*|reconnect\w*|follow[\s-]?up|contact\w*)\b",
                                 re.IGNORECASE)


@dataclass
class TimelineResult:
    category: str = NONE
    raw_text: str = ""            # exact substring of the remark
    months_estimate: float | None = None   # scoring only - never displayed as a date
    source: str = "NONE"          # LATEST_REMARK / PREVIOUS_REMARK / NONE
    evidence: str = ""            # the sentence(s) the raw_text comes from (exact substring)
    reason: str = ""              # which rule fired (for explanations)

    def as_dict(self) -> dict:
        return {"category": self.category, "raw_text": self.raw_text,
                "months_estimate": self.months_estimate, "source": self.source}

    @property
    def display(self) -> str:
        """Human text for the Timeline column - the client's own words + category."""
        return "Not Available" if self.category == NONE else f"{LABELS[self.category]}: {self.raw_text}"


# ---------------------------------------------------------------- helpers
def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]


def _clauses(sentence: str) -> list[str]:
    return [c.strip() for c in re.split(r"\s*[,;]\s*|\s+but\s+", sentence) if c.strip()]


def _months_between(contact: date | None, month_name: str, year: int) -> float | None:
    if not contact:
        return None
    return (year * 12 + MONTHS.index(month_name.lower()) + 1) - (contact.year * 12 + contact.month)


def _upper(a: str, b: str | None) -> float:
    return float(b) if b else float(a)


def _window(category_if_firm: str, sentence: str, months: float | None) -> str:
    """CONFIRMED vs TENTATIVE vs LONG_DELAY for a dated window."""
    if months is not None and months > LONG_DELAY_MONTHS:
        return LONG_DELAY
    return TENTATIVE if HEDGE.search(sentence) else category_if_firm


def _candidates_in_sentence(sentence: str, contact: date | None) -> list[TimelineResult]:
    found = []

    def add(category, match_text, months, reason):
        found.append(TimelineResult(category, match_text.strip(), months, evidence=sentence, reason=reason))

    for clause in _clauses(sentence):
        # 1. A pause the client asked for ("not to follow up for the next 6 months") - checked
        #    before the call-back filter because it is still a buying signal
        m = NEGATIVE_PAUSE.search(clause)
        if m:
            months = int(m.group(1)) * (12 if m.group(2).lower().startswith("year") else 1)
            add(LONG_DELAY if months >= LONG_DELAY_MONTHS else VAGUE, m.group(0), months, "negative pause")
            continue
        if NOT_PURCHASE_CLAUSE.search(clause):
            continue
        # 2. UNCERTAIN (before any number)
        m = UNCERTAIN_ALWAYS.search(clause)
        if not m:
            g = UNCERTAIN_GENERIC.search(clause)
            # doubt counts if the clause is about timing, or if it is about nothing else and the
            # sentence talks about timing ("Uncertain, but maybe within 1 month" -> UNCERTAIN)
            about_timing = TIMING_CUE.search(clause) or (
                not OTHER_SUBJECT.search(clause)
                and (TIMING_CUE.search(sentence) or any(p.search(sentence) for p in WINDOW_PATTERNS)))
            m = g if g and about_timing else None
        if m:
            add(UNCERTAIN, m.group(0), None, "uncertain wording")
            continue
        # 3. LONG_DELAY phrases
        m = LONG_DELAY_PHRASE.search(clause)
        if m:
            add(LONG_DELAY, m.group(0), 12.0, "long-delay wording")
            continue
        # 4. VAGUE (before any number)
        m = VAGUE_PHRASE.search(clause)
        if m:
            add(VAGUE, m.group(0), None, "vague wording")
            continue
        # 5. Month + year written in the remark
        m = MONTH_YEAR_RE.search(clause)
        if m:
            months = _months_between(contact, m.group(1), int(m.group(2)))
            negative = bool(re.search(r"\b(not|no)\b", m.group(0), re.IGNORECASE)) and "before" in m.group(0).lower()
            if negative:  # "not ... before December 2027": only an earliest date
                cat = LONG_DELAY if months is not None and months > LONG_DELAY_MONTHS else VAGUE
            else:
                cat = _window(CONFIRMED, sentence, months)
            add(cat, m.group(0), months, "month and year in remark")
            continue
        # 6. Numeric windows
        for pattern in WINDOW_PATTERNS:
            m = pattern.search(clause)
            if m:
                a, b, unit = m.group(1), m.group(2), m.group(3).lower()
                months = _upper(a, b) * UNIT_MONTHS[unit]
                hedged = bool(re.match(r"(maybe|probably|around|about|roughly|approx|likely)", m.group(0), re.I))
                cat = TENTATIVE if hedged and months <= LONG_DELAY_MONTHS else _window(CONFIRMED, sentence, months)
                add(cat, m.group(0), round(months, 2), "numeric window")
                break
        else:
            # 7. Relative phrases ("this month", "next week")
            m = RELATIVE_RE.search(clause)
            if m:
                months = next(v for p, v in RELATIVE if re.fullmatch(p, m.group(1), re.IGNORECASE))
                add(_window(CONFIRMED, sentence, months), m.group(0), months, "relative period")
    return found


def classify_remark(text: str, source: str = LATEST, contact: date | None = None) -> TimelineResult:
    """Classify ONE remark. The most cautious statement in the remark wins."""
    text = text or ""
    candidates: list[TimelineResult] = []

    # "call in January 2027 ... No purchase before that." -> earliest date only
    stop = NO_PURCHASE_BEFORE_THAT.search(text)
    if stop:
        dates = [m for m in re.finditer(MONTH_YEAR, text[:stop.start()], re.IGNORECASE)]
        if dates:
            d = dates[-1]
            months = _months_between(contact, d.group(1), int(d.group(2)))
            cat = LONG_DELAY if months is not None and months > LONG_DELAY_MONTHS else VAGUE
            sents = _sentences(text)
            first = next(i for i, s in enumerate(sents) if d.group(0) in s)
            last = next(i for i, s in enumerate(sents) if NO_PURCHASE_BEFORE_THAT.search(s))
            evidence = " ".join(sents[first:last + 1])
            candidates.append(TimelineResult(cat, text[d.start():stop.end()], months, evidence=evidence,
                                             reason="no purchase before a stated date"))

    for sentence in _sentences(text):
        candidates.extend(_candidates_in_sentence(sentence, contact))
    if not candidates:
        return TimelineResult(source="NONE")
    best = min(candidates, key=lambda c: PRIORITY[c.category])  # stable: first in text on ties
    best.source = source
    return best


def parse_contact_date(value: str) -> date | None:
    try:
        y, m, d = (int(x) for x in str(value)[:10].split("-"))
        return date(y, m, d)
    except Exception:
        return None


def resolve_timeline(lead: dict) -> dict:
    """LATEST-OVERRIDES-PREVIOUS.

    * Previous and latest remarks are classified separately.
    * If the latest remark has any category other than NONE it is the effective timeline.
      The previous one is kept as history and, if different, raises TIMELINE_CHANGED.
    * Only if the latest remark is NONE is the previous timeline used (source PREVIOUS_REMARK).
    * The previous timeline never adds points when the latest remark changes it.
    """
    contact = parse_contact_date(lead.get("Last_Contact_Date", ""))
    latest = classify_remark(lead.get("Latest_Remarks", ""), LATEST, contact)
    previous = classify_remark(lead.get("Previous_Remarks", ""), PREVIOUS, contact)

    history, flag, ignored_previous = "", None, None
    if latest.category != NONE:
        effective = latest
        if previous.category != NONE:
            history = f'{previous.category} "{previous.raw_text}" (previous remark) - superseded by latest remark'
            if (previous.category, previous.raw_text.lower()) != (latest.category, latest.raw_text.lower()):
                flag = (f'TIMELINE_CHANGED: "{previous.raw_text}" (previous remark) → '
                        f'"{latest.raw_text}" (latest remark)')
                ignored_previous = previous
    elif previous.category != NONE:
        effective = previous
    else:
        effective = TimelineResult(source="NONE")
    return {"effective": effective, "latest": latest, "previous": previous,
            "history": history, "conflict_flag": flag, "ignored_previous": ignored_previous}
