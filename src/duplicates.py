"""Duplicate lead detection (bonus).

Primary signal : same normalized phone (last 10 digits) -> duplicate.
Supporting signal: fuzzy name similarity (difflib ratio > 0.85).
A name-only match is reported as a *possible* duplicate for a human to check.
"""
from __future__ import annotations

from difflib import SequenceMatcher

import pandas as pd

NAME_SIMILARITY_THRESHOLD = 0.85


def name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def find_duplicates(df: pd.DataFrame) -> dict:
    """Return {lead_id: {"duplicate_of": "<other lead id>", "reason": "..."}}.

    Needs columns Lead_ID, Client_Name, Phone_Normalized. The earlier lead in
    the sheet is treated as the original; both sides point to each other.
    """
    result: dict[str, dict] = {}
    rows = df[["Lead_ID", "Client_Name", "Phone_Normalized"]].to_dict("records")

    def link(a, b, reason):
        result.setdefault(a["Lead_ID"], {"duplicate_of": [], "reason": []})
        result[a["Lead_ID"]]["duplicate_of"].append(b["Lead_ID"])
        result[a["Lead_ID"]]["reason"].append(reason)

    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            sim = name_similarity(a["Client_Name"], b["Client_Name"])
            same_phone = bool(a["Phone_Normalized"]) and a["Phone_Normalized"] == b["Phone_Normalized"]
            if same_phone:
                reason = f"Same phone ({a['Phone_Normalized']}); name similarity {sim:.2f}"
            elif sim > NAME_SIMILARITY_THRESHOLD:
                reason = f"Possible duplicate: name similarity {sim:.2f} (phones differ)"
            else:
                continue
            link(b, a, reason)  # later lead points to the original
            link(a, b, reason)  # original also shows the link

    return {k: {"duplicate_of": ", ".join(v["duplicate_of"]), "reason": " | ".join(v["reason"])}
            for k, v in result.items()}
