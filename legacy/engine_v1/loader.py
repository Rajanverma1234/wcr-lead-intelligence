"""Step 1 of the pipeline: read the Excel file and clean it up.

- Reads the Lead_Data sheet as text (so phone numbers keep their leading 0).
- Trims spaces, replaces empty cells with "".
- Adds a normalized phone column used for duplicate detection.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# Project root = the folder that contains src/ (never a hard-coded absolute path)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "data" / "WCR_AI_Intern_Practical_Candidate_Pack.xlsx"
OUTPUT_DIR = PROJECT_ROOT / "output"
SHEET_NAME = "Lead_Data"

REQUIRED_COLUMNS = [
    "Lead_ID", "Client_Name", "Phone", "Market", "Last_Contact_Date",
    "Current_Status", "Recorded_Project", "Recorded_Budget",
    "Previous_Remarks", "Latest_Remarks",
]


def normalize_phone(phone: str) -> str:
    """'+91 98100 11001', '09810011001', '+91-98100-11001' -> '9810011001'.

    Keep digits only, then keep the last 10 digits (drops +91 / leading 0).
    """
    digits = re.sub(r"\D", "", str(phone or ""))
    return digits[-10:] if len(digits) >= 10 else digits


def _clean_date(value: str) -> str:
    """Excel dates arrive as '2026-08-16 00:00:00'; keep just 'YYYY-MM-DD'."""
    match = re.match(r"\d{4}-\d{2}-\d{2}", value)
    return match.group(0) if match else value


def load_leads(source=None) -> pd.DataFrame:
    """Load leads from a path or an uploaded file object (Streamlit upload).

    Returns a DataFrame with all REQUIRED_COLUMNS as clean strings plus
    'Phone_Normalized'.
    """
    source = source if source is not None else DEFAULT_INPUT
    df = pd.read_excel(source, sheet_name=SHEET_NAME, dtype=str)

    # Normalize column names: strip spaces, spaces -> underscores
    df.columns = [str(c).strip().replace(" ", "_") for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Lead_Data sheet is missing columns: {missing}")

    df = df[REQUIRED_COLUMNS].fillna("")
    for col in REQUIRED_COLUMNS:
        df[col] = df[col].astype(str).str.strip()
    df = df[df["Lead_ID"] != ""].reset_index(drop=True)  # drop blank rows

    df["Last_Contact_Date"] = df["Last_Contact_Date"].map(_clean_date)
    df["Phone_Normalized"] = df["Phone"].map(normalize_phone)
    return df


def build_source_text(row) -> str:
    """All text the system is ALLOWED to use for one lead.

    Every piece of evidence and every fact in a WhatsApp message must be a
    substring of this text. Structured columns are labelled so evidence such
    as 'Recorded_Project: M3M BRABUS' can be verified the same way.
    """
    return " ".join([
        str(row.get("Previous_Remarks", "")),
        str(row.get("Latest_Remarks", "")),
        f"Recorded_Project: {row.get('Recorded_Project', '')}",
        f"Recorded_Budget: {row.get('Recorded_Budget', '')}",
        f"Market: {row.get('Market', '')}",
        f"Client_Name: {row.get('Client_Name', '')}",
    ])
