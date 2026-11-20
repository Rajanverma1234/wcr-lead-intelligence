"""Next action + WhatsApp message - template based, filled ONLY from extracted facts.

Every message is checked by validator.validate_message(); if it mentions any
number / date / project / place not in the row, we fall back to a generic
safe template that contains no facts at all.
"""
from __future__ import annotations

import re

from .extractor import NA
from .validator import validate_message

NO_CONTACT_MESSAGE = "Not Applicable – client asked not to be contacted for now"


def first_name(client_name: str) -> str:
    return (client_name or "").split()[0] if client_name else "there"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _dependency_labels(fields) -> list[str]:
    return [label for label, _ in fields["Objection_or_Dependency"].meta.get("items", [])]


def next_action(lead, fields, signals, duplicate_of: str = "") -> str:
    """Rule-based next step for the sales person."""
    timeline = fields["Timeline"]
    visit = fields["Site_Visit_Status"].meta.get("status")
    deps = " ".join(_dependency_labels(fields)).lower()
    materials = [label for label, _ in signals["materials"]]
    actions: list[str] = []

    # 1. Client asked not to be contacted -> respect it
    if signals.get("no_contact"):
        months = timeline.meta.get("months")
        period = f"{months} months" if months else "the period the client stated"
        actions.append(f"Do not follow up for {period} as the client requested; set a reminder for after that period")
    # 2. No plan to buy / not interested
    elif signals.get("not_interested"):
        msg = "Do not push; client has no plan to buy right now"
        if materials:
            msg += f". Send only the requested {_join(materials)}"
        actions.append(msg + "; move to long-term nurture")
    # 3. Long delay stated by the client
    elif timeline.meta.get("negative") and (timeline.meta.get("horizon_days") or 0) >= 180:
        actions.append(f"Do not push now ({timeline.value}); keep in nurture and set a reminder")
        if visit == "Not Now":
            actions.append("do not push for a site visit")
    else:
        if signals.get("negotiation"):
            actions.append("Share the best final offer today and move to close")
        if materials:
            when = " today" if signals.get("today") and any(m in signals["today"] for m in materials) else ""
            actions.append(f"Send {_join(materials)}{when}")
        if visit == "Confirmed":
            when = fields["Site_Visit_Status"].meta.get("when", "")
            actions.append(f"confirm the site visit ({when.removeprefix('on ')})" if when else "reconfirm the site visit")
        elif visit == "Tentative":
            extra = " once the flight is confirmed" if "flight" in deps else ""
            actions.append(f"follow up to fix a firm site-visit day and time{extra}")
        elif visit == "Completed" and not signals.get("negotiation"):
            actions.append("take feedback on the site visit")
        if "sell" in deps:
            actions.append("track the property sale and follow up on the timeline")
        if "family" in deps or "wife" in deps:
            actions.append("involve the family decision-maker in the next call/visit")
        if "mismatch" in deps:
            actions.append("share alternative projects that fit the stated budget")
        if "rera" in deps and not any("RERA" in m for m in materials):
            actions.append("share legal/RERA details")
        if "numbers" in deps:
            actions.append("prepare the investment numbers (exit/rental case)")
        if signals.get("callback"):
            m = re.search(r"(call later|call next week|call in \w+ \d{4}|reconnect after \d+ weeks)",
                          signals["callback"], re.IGNORECASE)
            actions.append(f"{m.group(1)} as requested" if m else "call back as requested")
        if signals.get("updates"):
            actions.append("keep sharing project updates")
        if all(fields[f].value == NA for f in ("Budget", "Timeline", "Purpose", "Projects_Discussed")):
            actions.append("call to capture budget, purpose, timeline and project interest")
        if not actions:
            actions.append("follow up to understand the requirement and next step")

    if duplicate_of:
        actions.append(f"possible duplicate of {duplicate_of} – merge records before calling")
    text = "; ".join(actions)
    return text[0].upper() + text[1:]


def suggested_whatsapp(lead, fields, signals) -> str:
    """Max ~2 sentences, client's first name, only facts present in the row."""
    if signals.get("no_contact"):
        return NO_CONTACT_MESSAGE

    name = first_name(lead.get("Client_Name", ""))
    visit_meta = fields["Site_Visit_Status"].meta
    visit = visit_meta.get("status")
    timeline = fields["Timeline"]
    deps = " ".join(_dependency_labels(fields)).lower()
    materials = [label for label, _ in signals["materials"]]
    location = fields["Location_Preference"]

    # ---- Sentence 1: what we are doing for the client
    if signals.get("negotiation"):
        first = f"Hi {name}, thank you for visiting the site. I am working on the best final offer for you and will share it today."
    elif visit == "Confirmed":
        when = visit_meta.get("when")
        first = f"Hi {name}, looking forward to your site visit{' ' + when if when else ''}."
        if materials:
            first += f" Sharing the {_join(materials)} as requested."
        return _safe(first, lead, name)
    elif materials:
        first = f"Hi {name}, sharing the {_join(materials)} you asked for."
    else:
        first = f"Hi {name}, thank you for your time."

    # ---- Sentence 2: one closing line chosen from the strongest fact
    if signals.get("negotiation"):
        second = ""
    elif signals.get("not_interested") or (timeline.meta.get("negative") and (timeline.meta.get("horizon_days") or 0) >= 180):
        second = "No rush at all – I will check in later, and feel free to reach out anytime."
    elif "flight" in deps:
        second = "Please let me know once your flight is confirmed so we can lock the site visit."
    elif "mismatch" in deps:
        second = "I will share options that fit your budget."
    elif "family" in deps or "wife" in deps:
        second = "Happy to answer any questions you and your family may have."
    elif visit == "Tentative":
        second = "Please let me know a convenient day and time for the site visit."
    elif timeline.value != NA and timeline.meta.get("phrase") and not timeline.meta.get("negative"):
        second = f"I will stay in touch regarding your plan to invest {timeline.meta['phrase']}."
    elif signals.get("updates") and location.source in ("latest", "previous"):
        second = f"I will keep sharing updates on {location.value} projects."
    elif signals.get("callback"):
        second = "When would be a good time to call you?"
    else:
        second = "Please let me know if you would like any more details."

    return _safe(f"{first} {second}".strip(), lead, name)


def _safe(message: str, lead, name: str) -> str:
    """Validator gate: any fact not in the row -> generic fact-free fallback."""
    from .loader import build_source_text
    if validate_message(message, build_source_text(lead)):
        return message
    return f"Hi {name}, following up on our recent conversation. Please let me know a good time to connect."
