"""Glue: load -> extract -> validate -> conflicts -> score -> messages.

    process_lead(row)  -> (output_row_dict, details_dict)
    process_all(df)    -> (output DataFrame, {lead_id: details})
"""
from __future__ import annotations

import pandas as pd

from .conflicts import detect_conflicts
from .duplicates import find_duplicates
from .extractor import NA, detect_signals, extract_all
from .loader import build_source_text
from .messaging import next_action, suggested_whatsapp
from .scoring import score_lead
from .validator import validate_field

OUTPUT_COLUMNS = [  # exact names from the Output_Template sheet
    "Lead_ID", "Budget", "Timeline", "Purpose", "Projects_Discussed", "Location_Preference",
    "Site_Visit_Status", "Objection_or_Dependency", "Next_Action", "Lead_Temperature",
    "Buying_Probability_0_100", "Confidence_0_100", "Evidence", "Suggested_WhatsApp",
]
EXTRA_COLUMNS = ["Client_Name", "Market", "Conflict_Flags", "Duplicate_Of", "Score_Breakdown",
                 # round 2
                 "Timeline_Category", "Timeline_History", "Dependencies_List"]
FIELD_LABELS = {  # short labels used inside the Evidence column
    "Budget": "Budget", "Timeline": "Timeline", "Purpose": "Purpose",
    "Projects_Discussed": "Projects", "Location_Preference": "Location",
    "Site_Visit_Status": "Visit", "Objection_or_Dependency": "Objection",
}


def _evidence_column(fields: dict) -> str:
    """'Budget/Purpose: "<quote>" | Visit: "<quote>"' - identical quotes merged."""
    by_quote: dict[str, list[str]] = {}
    for name, result in fields.items():
        if result.value == NA:
            continue
        for quote in result.evidence:
            by_quote.setdefault(quote, []).append(FIELD_LABELS[name])
    return " | ".join(f'{"/".join(labels)}: "{quote}"' for quote, labels in by_quote.items()) or NA


def _dependencies_column(fields: dict) -> str:
    deps = [i for i in fields["Objection_or_Dependency"].meta.get("list", []) if i["kind"] == "dependency"]
    return " | ".join(f'{i["label"]} ("{i["evidence"]}")' for i in deps) or "None"


def process_lead(row, duplicate_of: str = "", llm_fields: dict | None = None,
                 duplicate_reason: str = "") -> tuple[dict, dict]:
    lead = {k: ("" if pd.isna(v) else str(v)) for k, v in dict(row).items()}
    source_text = build_source_text(lead)

    # 1. Rule-based extraction, then 2. validator (evidence must exist in the row)
    fields = {name: validate_field(res, source_text) for name, res in extract_all(lead).items()}

    # Optional LLM values replace rule values ONLY if they passed the same validator
    display_fields = dict(fields)
    if llm_fields:
        for name, res in llm_fields.items():
            if name in display_fields and res.value != NA:
                display_fields[name] = res

    # 3. Conflicts (latest wins, but recorded) -> lower that field's confidence by 10
    conflicts = detect_conflicts(lead, fields)
    if duplicate_of and "Same phone" in duplicate_reason:
        conflicts.setdefault("Duplicate", []).append(f"DUPLICATE_PHONE: same phone as {duplicate_of}")
    for name in conflicts:
        if name in display_fields:
            display_fields[name].confidence = max(0, display_fields[name].confidence - 10)

    # 4. Scoring (always from rule-based fields = source of truth)
    signals = detect_signals(lead)
    score = score_lead(lead, fields, signals)

    # 5. Messages
    action = next_action(lead, fields, signals, duplicate_of)
    whatsapp = suggested_whatsapp(lead, fields, signals)

    confidence = round(sum(f.confidence for f in display_fields.values()) / len(display_fields))
    conflict_text = " | ".join(flag for flags in conflicts.values() for flag in flags)

    out = {
        "Lead_ID": lead["Lead_ID"],
        **{name: res.value for name, res in display_fields.items()},
        "Next_Action": action,
        "Lead_Temperature": score["temperature"],
        "Buying_Probability_0_100": score["probability"],
        "Confidence_0_100": confidence,
        "Evidence": _evidence_column(display_fields),
        "Suggested_WhatsApp": whatsapp,
        "Client_Name": lead.get("Client_Name", ""),
        "Market": lead.get("Market", ""),
        "Conflict_Flags": conflict_text or "None",
        "Duplicate_Of": duplicate_of or "None",
        "Score_Breakdown": " | ".join(score["breakdown"]),
        "Timeline_Category": fields["Timeline"].meta["category"],
        "Timeline_History": fields["Timeline"].meta["resolution"]["history"] or "None",
        "Dependencies_List": _dependencies_column(fields),
    }
    details = {
        "fields": {name: res.as_dict() for name, res in display_fields.items()},
        "rule_fields": {name: res.as_dict() for name, res in fields.items()},
        "conflicts": conflicts,
        "score_breakdown": score["breakdown"],
        "source_text": source_text,
        "signals": {k: v for k, v in signals.items() if v},
    }
    return {c: out[c] for c in OUTPUT_COLUMNS + EXTRA_COLUMNS}, details


def process_all(df: pd.DataFrame, use_llm: bool = False, llm_provider: str = "anthropic") -> tuple[pd.DataFrame, dict]:
    duplicates = find_duplicates(df)
    rows, details = [], {}
    for _, row in df.iterrows():
        lead_id = row["Lead_ID"]
        dup = duplicates.get(lead_id, {})
        llm_fields = None
        if use_llm:
            from .llm_optional import llm_extract_validated
            llm_fields = llm_extract_validated(dict(row), provider=llm_provider)
        out, det = process_lead(row, dup.get("duplicate_of", ""), llm_fields, dup.get("reason", ""))
        det["duplicate_reason"] = dup.get("reason", "")
        rows.append(out)
        details[lead_id] = det
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS + EXTRA_COLUMNS), details


PRIORITY_COLUMNS = [
    "Rank", "Lead_ID", "Client_Name", "Market", "Lead_Temperature", "Buying_Probability_0_100",
    "Confidence_0_100", "Budget", "Timeline", "Site_Visit_Status", "Objection_or_Dependency",
    "Next_Action", "Score_Breakdown",
]


def priority_list(out_df: pd.DataFrame) -> pd.DataFrame:
    """Leads from highest to lowest priority.

    Sort: Buying probability (high first) -> Confidence (high first) -> Lead_ID.
    """
    ranked = out_df.sort_values(["Buying_Probability_0_100", "Confidence_0_100", "Lead_ID"],
                                ascending=[False, False, True]).reset_index(drop=True)
    ranked.insert(0, "Rank", range(1, len(ranked) + 1))
    return ranked[PRIORITY_COLUMNS]


def to_excel_bytes(out_df: pd.DataFrame) -> bytes:
    """Workbook with two sheets: All_Leads (Lead_ID order) and Priority_List (ranked)."""
    import io
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet, df in (("All_Leads", out_df), ("Priority_List", priority_list(out_df))):
            df.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            ws.freeze_panes = "B2"  # keep header row and first column visible
            for col_cells in ws.columns:  # readable column widths (capped)
                longest = max(len(str(c.value or "")) for c in col_cells)
                ws.column_dimensions[col_cells[0].column_letter].width = min(max(10, longest + 2), 60)
    return buffer.getvalue()


def save_outputs(out_df: pd.DataFrame, output_dir) -> tuple:
    """Write processed_leads.csv, priority_list.csv (utf-8-sig so ₹ opens in Excel) and processed_leads.xlsx."""
    from pathlib import Path
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path, xlsx_path = output_dir / "processed_leads.csv", output_dir / "processed_leads.xlsx"
    out_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    priority_list(out_df).to_csv(output_dir / "priority_list.csv", index=False, encoding="utf-8-sig")
    xlsx_path.write_bytes(to_excel_bytes(out_df))
    return csv_path, xlsx_path
