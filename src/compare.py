"""Before vs After: legacy engine (round 1, frozen in legacy/engine_v1) vs current engine.

build_comparison(df, dataset) runs BOTH engines live on the same leads and explains each
difference from the two Score_Breakdowns - nothing is pasted or hard-coded.
"""
from __future__ import annotations

import re

import pandas as pd

from legacy.engine_v1.pipeline import process_all as legacy_process_all

from .pipeline import process_all

SKIP_LINE_PREFIXES = ("Base score", "Final:", "Cold confirmed", "Override", "Clamped")
FIELDS_TO_DIFF = ["Budget", "Purpose", "Projects_Discussed", "Location_Preference", "Site_Visit_Status"]
COLUMNS = ["Dataset", "Lead_ID", "Score_Before", "Score_After", "Score_Change", "Temp_Before", "Temp_After",
           "Timeline_Before", "Timeline_After", "Dependencies_Before", "Dependencies_After",
           "Conflict_Flags_Before", "Conflict_Flags_After", "Changed_Because"]


def _score_lines(breakdown: str) -> list[str]:
    return [line for line in breakdown.split(" | ") if line and not line.startswith(SKIP_LINE_PREFIXES)]


def _points(line: str) -> int:
    m = re.search(r"→ ([+-]\d+) pts", line) if line.startswith("Timeline:") else re.match(r"([+-]\d+)", line)
    return int(m.group(1)) if m else 0


def _is_timeline(line: str) -> bool:
    return line.startswith("Timeline:") or bool(re.match(r"[+-]\d+ (timeline|long delay)", line))


def _changed_because(before: pd.Series, after: pd.Series) -> str:
    old, new = _score_lines(before["Score_Breakdown"]), _score_lines(after["Score_Breakdown"])
    reasons = []

    # 1. timeline points (old day-based rule vs new category rule)
    old_t = sum(_points(line) for line in old if _is_timeline(line))
    new_t_lines = [line for line in new if _is_timeline(line)]
    new_t = sum(_points(line) for line in new_t_lines)
    if old_t != new_t:
        reasons.append(f"timeline {old_t:+d} → {new_t:+d} ({new_t_lines[0].removeprefix('Timeline: ')})")

    # 2. number of dependencies (labels were renamed, so compare counts)
    old_d = [line for line in old if "dependency:" in line]
    new_d = [line for line in new if "dependency:" in line]
    if len(old_d) != len(new_d):
        labels = ", ".join(line.split("dependency: ", 1)[1] for line in new_d) or "none"
        reasons.append(f"dependencies {len(old_d)} → {len(new_d)} ({labels}) = {10 * (len(old_d) - len(new_d)):+d}")

    # 3. every other scoring rule that fired only before or only after
    rest_old = [line for line in old if not _is_timeline(line) and "dependency:" not in line]
    rest_new = [line for line in new if not _is_timeline(line) and "dependency:" not in line]
    reasons += [f"no longer scored: {line}" for line in rest_old if line not in rest_new]
    reasons += [f"newly scored: {line}" for line in rest_new if line not in rest_old]

    # 4. field values that changed (score-neutral but visible in the output)
    for f in FIELDS_TO_DIFF:
        if before[f] != after[f]:
            reasons.append(f'{f}: "{before[f]}" → "{after[f]}"')
    return "; ".join(reasons) or "No change"


def _dependency_count(objection: str) -> str:
    return objection if objection and objection != "Not Available" else "None"


def build_comparison(df: pd.DataFrame, dataset: str) -> pd.DataFrame:
    before, _ = legacy_process_all(df)
    after, _ = process_all(df)
    before, after = before.set_index("Lead_ID", drop=False), after.set_index("Lead_ID", drop=False)
    rows = []
    for lead_id in after.index:
        b, a = before.loc[lead_id], after.loc[lead_id]
        rows.append({
            "Dataset": dataset, "Lead_ID": lead_id,
            "Score_Before": int(b["Buying_Probability_0_100"]), "Score_After": int(a["Buying_Probability_0_100"]),
            "Score_Change": int(a["Buying_Probability_0_100"]) - int(b["Buying_Probability_0_100"]),
            "Temp_Before": b["Lead_Temperature"], "Temp_After": a["Lead_Temperature"],
            "Timeline_Before": b["Timeline"], "Timeline_After": f'{a["Timeline"]} [{a["Timeline_Category"]}]',
            "Dependencies_Before": _dependency_count(b["Objection_or_Dependency"]),
            "Dependencies_After": a["Dependencies_List"],
            "Conflict_Flags_Before": b["Conflict_Flags"], "Conflict_Flags_After": a["Conflict_Flags"],
            "Changed_Because": _changed_because(b, a),
        })
    return pd.DataFrame(rows, columns=COLUMNS)


def to_markdown(table: pd.DataFrame) -> str:
    def cell(v) -> str:
        return str(v).replace("|", "\\|").replace("\n", " ")
    lines = ["# Before vs After (legacy engine_v1 → round 2 engine)", "",
             f"Generated live by `scripts/compare_before_after.py` – {len(table)} leads.", ""]
    for dataset, part in table.groupby("Dataset", sort=False):
        changed = part[part["Score_Change"] != 0]
        temp_changed = part[part["Temp_Before"] != part["Temp_After"]]
        lines += [f"## {dataset}", "",
                  f"Score changed for {len(changed)} of {len(part)} leads; temperature changed for "
                  f"{len(temp_changed)}.", "",
                  "| " + " | ".join(COLUMNS[1:]) + " |", "|" + "---|" * (len(COLUMNS) - 1)]
        lines += ["| " + " | ".join(cell(r[c]) for c in COLUMNS[1:]) + " |" for _, r in part.iterrows()]
        lines.append("")
    return "\n".join(lines)
