"""WhatsApp validation (round 2).

validate_whatsapp(message, validated_fields, ...) -> list of reasons (empty list = PASS)

A message passes only if:
  1. every project, amount/number, date/time word, time window and location in it
     appears in a VALIDATED field of this lead (value != "Not Available") or in the
     evidence of an explicit request (materials / call-back) from this lead;
  2. it does not present an UNCERTAIN / VAGUE / missing timeline as a firm window;
  3. it promises no discount, price, offer price or availability;
  4. it mentions no other lead's client name;
  5. if the lead has dependencies, it acknowledges them politely ("no rush", "depends on", ...).

fix_whatsapp() runs the check and, on failure, regenerates the message from a safe
template that uses only validated fields, returning (message, "PASS" | "FIXED: reasons").
"""
from __future__ import annotations

import re

from .extractor import KNOWN_LOCATIONS, KNOWN_PROJECTS, NA

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
DATE_WORDS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
              "Saturday", "Sunday"]
RELATIVE_DAY = re.compile(r"\b(today|tomorrow|tonight|this weekend|next weekend)\b", re.IGNORECASE)
WINDOW = re.compile(
    r"\b(?:within|in|by|over the next|in the next)\s+\d+(?:\.\d+)?(?:\s*(?:–|-|to)\s*\d+)?\s*(?:days?|weeks?|months?|years?)\b"
    r"|\b(?:this|next)\s+(?:week|month|quarter)\b|\bsoon\b|\bshortly\b|\bimmediately\b|\bright away\b",
    re.IGNORECASE)
CURRENCY = re.compile(r"₹|\bRs\.?|\bINR\b|\bAED\b|\bCr\b|\bcrores?\b|\blakhs?\b", re.IGNORECASE)
PROMISE = re.compile(
    r"\b(discount\w*|special price|special offer|best price|lowest price|price drop|cashback|freebies?|free (?:gift|parking|registration|upgrade|stamp duty)|"
    r"guarantee\w*|assured returns?|limited (?:units|period|time)|only \d+ units? left|few units left|"
    r"units? (?:is|are) (?:still )?available|still available|in stock|price (?:will|is going to) (?:rise|increase))\b",
    re.IGNORECASE)
ACK = re.compile(r"no rush|no pressure|whenever you|take your time|depends on|once your|your pace|until then",
                 re.IGNORECASE)
FIRM_OK = {"CONFIRMED"}  # only a CONFIRMED timeline may be repeated back as a window

# Client-friendly words for each dependency type (generic on purpose - no extra facts)
DEPENDENCY_PHRASES = {
    "PROPERTY_SALE": "your property sale", "FAMILY_APPROVAL": "a family discussion",
    "HOME_LOAN": "your home loan approval", "NRI_FUNDS": "your fund transfer",
    "POSSESSION": "the handover of your other property", "JOB_TRANSFER": "your job/transfer confirmation",
    "PAYMENT_PLAN": "the payment plan", "DETAILS_BEFORE_VISIT": "the details you asked for",
    "FUNDS_TIED_UP": "your funds becoming free", "BONUS": "your annual bonus",
    "FLIGHT": "your flight confirmation", "NUMBERS": "the investment numbers",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def allowed_text(validated_fields: dict, extra_evidence: list[str] | None = None) -> str:
    parts = []
    for f in validated_fields.values():
        if f.value != NA:
            parts.append(f.value)
            parts.extend(f.evidence)
    parts.extend(extra_evidence or [])
    return " ".join(parts)


def dependency_types(validated_fields: dict) -> list[str]:
    obj = validated_fields.get("Objection_or_Dependency")
    items = obj.meta.get("list", []) if obj else []
    return [i["type"] for i in items if i["kind"] == "dependency"]


def validate_whatsapp(message: str, validated_fields: dict, extra_evidence: list[str] | None = None,
                      other_names: list[str] | tuple = (), client_name: str = "") -> list[str]:
    reasons = []
    allowed = allowed_text(validated_fields, extra_evidence)
    allowed_n = _norm(allowed)

    # 1. facts
    for name, pattern in KNOWN_PROJECTS.items():
        if re.search(pattern, message, re.IGNORECASE) and not re.search(pattern, allowed, re.IGNORECASE):
            reasons.append(f"project not in validated fields: {name}")
    without_projects = message
    for pattern in KNOWN_PROJECTS.values():
        without_projects = re.sub(pattern, " ", without_projects, flags=re.IGNORECASE)
    for number in NUMBER_RE.findall(without_projects):
        if number not in allowed:
            reasons.append(f"number/amount not in validated fields: {number}")
    for m in CURRENCY.finditer(message):
        if m.group(0).lower() not in allowed_n:
            reasons.append(f"currency not in validated fields: {m.group(0)}")
    for word in DATE_WORDS:
        if re.search(r"\b" + word + r"\b", message) and not re.search(r"\b" + word + r"\b", allowed):
            reasons.append(f"date word not in validated fields: {word}")
    for m in RELATIVE_DAY.finditer(message):
        if m.group(0).lower() not in allowed_n:
            reasons.append(f"time reference not in validated fields: {m.group(0)}")
    for loc in KNOWN_LOCATIONS:
        if re.search(r"\b" + re.escape(loc) + r"\b", message, re.IGNORECASE) and loc.lower() not in allowed_n:
            reasons.append(f"location not in validated fields: {loc}")

    # 2. timeline must not be presented as firm unless it is CONFIRMED
    timeline = validated_fields.get("Timeline")
    category = timeline.meta.get("category", "NONE") if timeline else "NONE"
    for m in WINDOW.finditer(message):
        if category not in FIRM_OK:
            reasons.append(f"presents a {category} timeline as firm: \"{m.group(0)}\"")
        elif _norm(m.group(0)) not in allowed_n:
            reasons.append(f"time window not in validated fields: \"{m.group(0)}\"")

    # 3. no promises on discounts / prices / availability
    for m in PROMISE.finditer(message):
        reasons.append(f"promise not allowed: \"{m.group(0)}\"")

    # 4. no other lead's data
    for other in other_names:
        if other and _norm(other) != _norm(client_name) and _norm(other) in _norm(message):
            reasons.append(f"mentions another lead's client: {other}")

    # 5. dependencies must be acknowledged without pressure
    if dependency_types(validated_fields) and not ACK.search(message):
        reasons.append("dependencies not acknowledged")
    return reasons


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def safe_template(first_name: str, validated_fields: dict, materials: list[str]) -> str:
    """Fact-light message built only from validated fields."""
    opening = (f"Hi {first_name}, sharing the {_join(materials)} you asked for." if materials
               else f"Hi {first_name}, thank you for your time.")
    phrases = list(dict.fromkeys(DEPENDENCY_PHRASES.get(t, "a few things on your side")
                                 for t in dependency_types(validated_fields)))
    if phrases:
        closing = f"No rush at all – I understand this depends on {_join(phrases)}, so I will follow your pace."
    else:
        closing = "Please let me know if you would like any more details."
    return f"{opening} {closing}"


def fix_whatsapp(message: str, first_name: str, validated_fields: dict, materials: list[str],
                 extra_evidence: list[str], other_names=(), client_name: str = "") -> tuple[str, str]:
    reasons = validate_whatsapp(message, validated_fields, extra_evidence, other_names, client_name)
    if not reasons:
        return message, "PASS"
    fixed = safe_template(first_name, validated_fields, materials)
    if validate_whatsapp(fixed, validated_fields, extra_evidence, other_names, client_name):
        fixed = safe_template(first_name, validated_fields, [])  # drop material names too
    return fixed, "FIXED: " + "; ".join(dict.fromkeys(reasons))
