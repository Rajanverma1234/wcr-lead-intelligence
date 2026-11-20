"""Shared negation check: is a keyword inside a negated clause?

"He is NOT looking to invest"   -> "invest" is negated
"no home loan needed"           -> "home loan" is negated
"Interested in Golf Course ... but has not agreed to a site visit" -> the location is NOT negated
(only words BEFORE the keyword in the same clause, at most 6 words back, are checked).
"""
from __future__ import annotations

import re

NEGATORS = re.compile(r"\b(not|no|don't|dont|do not|never|nahi|nahin|isn't|won't|without)\b", re.IGNORECASE)
CLAUSE_BREAK = re.compile(r"[,;:.!?]|\bbut\b|\band\b", re.IGNORECASE)
WINDOW_WORDS = 6


def is_negated(text: str, keyword_start: int) -> bool:
    before = text[:keyword_start]
    breaks = list(CLAUSE_BREAK.finditer(before))
    clause_before = before[breaks[-1].end():] if breaks else before
    window = " ".join(clause_before.split()[-WINDOW_WORDS:])
    return bool(NEGATORS.search(window))
