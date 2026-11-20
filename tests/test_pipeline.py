"""Acceptance tests - run with:  pytest -v"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.extractor import NA  # noqa: E402
from src.loader import build_source_text, load_leads, normalize_phone  # noqa: E402
from src.pipeline import OUTPUT_COLUMNS, process_all  # noqa: E402
from src.validator import NUMBER_RE, unsupported_facts  # noqa: E402


@pytest.fixture(scope="module")
def result():
    leads = load_leads()
    out, details = process_all(leads)
    return leads, out.set_index("Lead_ID", drop=False), details


def row(result, lead_id):
    return result[1].loc[lead_id]


# ------------------------------------------------------------ structure
def test_all_leads_and_columns(result):
    leads, out, _ = result
    assert len(out) == 24
    for col in OUTPUT_COLUMNS + ["Client_Name", "Market", "Conflict_Flags", "Duplicate_Of", "Score_Breakdown"]:
        assert col in out.columns


def test_phone_normalization():
    assert normalize_phone("+91 98100 11001") == "9810011001"
    assert normalize_phone("09810011005") == "9810011005"
    assert normalize_phone("+91-98100-11003") == "9810011003"


# ------------------------------------------------------------ per-lead acceptance
def test_L001(result):
    r = row(result, "L001")
    assert r.Budget == "₹5–6 Cr"
    assert r.Purpose == "Investment"
    assert r.Location_Preference == "Golf Course Extension Road"
    assert "Noida" not in r.Location_Preference
    assert "noida" in r.Objection_or_Dependency.lower() and "sell" in r.Objection_or_Dependency.lower()
    assert "this week" in r.Site_Visit_Status.lower() and "Confirmed" not in r.Site_Visit_Status


def test_L002(result):
    r = row(result, "L002")
    assert r.Budget == "Up to ₹6.5 Cr"
    assert "4 Cr" in r.Conflict_Flags and "6.5 Cr" in r.Conflict_Flags
    assert r.Purpose == "End Use"
    assert r.Site_Visit_Status.startswith("Confirmed")
    assert "Sunday" in r.Site_Visit_Status and "12 PM" in r.Site_Visit_Status
    assert r.Lead_Temperature == "Hot"


def test_L003(result):
    r = row(result, "L003")
    assert r.Lead_Temperature == "Cold"
    assert "December 2027" in r.Timeline
    assert r.Site_Visit_Status.startswith("Not Now")
    assert r.Budget == NA


def test_L004(result):
    r = row(result, "L004")
    assert r.Site_Visit_Status == "Tentative"
    obj = r.Objection_or_Dependency.lower()
    assert "payment plan" in obj and "family" in obj


def test_L005(result):
    r = row(result, "L005")
    for col in ["Budget", "Projects_Discussed", "Purpose", "Timeline"]:
        assert r[col] == NA, col
    assert r.Lead_Temperature == "Cold"


def test_L008(result):
    r = row(result, "L008")
    assert r.Lead_Temperature == "Cold"
    assert "do not follow up" in r.Next_Action.lower() and "6 months" in r.Next_Action
    assert r.Suggested_WhatsApp.startswith("Not Applicable")


def test_L009(result):
    r = row(result, "L009")
    assert r.Lead_Temperature == "Hot"
    assert r.Site_Visit_Status.startswith("Confirmed")
    assert "₹8 Cr" in r.Budget
    assert r.Purpose == "End Use"


def test_L010(result):
    obj = row(result, "L010").Objection_or_Dependency.lower()
    assert "budget" in obj and ("mismatch" in obj or "stretch" in obj)


def test_L013_no_fake_date(result):
    t = row(result, "L013").Timeline
    assert "next year" in t.lower()
    assert not re.search(r"\d", t), "timeline must not contain any month/year number"
    assert not re.search(r"January|February|March|April|June|July|August|September|October|November|December", t)


def test_L014(result):
    r = row(result, "L014")
    assert "AED 2M" in r.Budget and "₹" not in r.Budget
    assert "₹5 Cr equivalent" in r.Conflict_Flags
    assert r.Purpose == "Investment"
    assert r.Location_Preference == "Dubai"


def test_L016(result):
    _, out, _ = result
    r = row(result, "L016")
    assert r.Site_Visit_Status == "Completed"
    assert r.Lead_Temperature == "Hot"
    assert r.Buying_Probability_0_100 >= out["Buying_Probability_0_100"].max() - 5


def test_L020(result):
    r = row(result, "L020")
    assert r.Lead_Temperature == "Cold"
    assert "quarter" in r.Conflict_Flags.lower() and "12 months" in r.Conflict_Flags


def test_L021_duplicate(result):
    assert row(result, "L021").Duplicate_Of == "L005"


def test_L023(result):
    assert row(result, "L023").Site_Visit_Status == "Tentative"


def test_L024(result):
    r = row(result, "L024")
    assert r.Site_Visit_Status == "Tentative"
    assert r.Lead_Temperature == "Hot" or r.Buying_Probability_0_100 >= 60


# ------------------------------------------------------------ global anti-hallucination tests
def test_every_evidence_is_in_source(result):
    leads, out, details = result
    for _, lead in leads.iterrows():
        source = build_source_text(lead).lower()
        # 1. field-level evidence
        for name, f in details[lead.Lead_ID]["fields"].items():
            if f["value"] != NA:
                assert f["evidence"], f"{lead.Lead_ID} {name} has a value but no evidence"
            for quote in f["evidence"]:
                assert quote.lower() in source, f"{lead.Lead_ID} {name}: {quote!r}"
        # 2. every quote inside the Evidence column
        for quote in re.findall(r'"([^"]+)"', out.loc[lead.Lead_ID, "Evidence"]):
            assert quote.lower() in source, f"{lead.Lead_ID} Evidence column: {quote!r}"


def test_whatsapp_only_uses_facts_from_row(result):
    leads, out, _ = result
    for _, lead in leads.iterrows():
        message = out.loc[lead.Lead_ID, "Suggested_WhatsApp"]
        source = build_source_text(lead)
        assert unsupported_facts(message, source) == [], f"{lead.Lead_ID}: {message}"
        for number in NUMBER_RE.findall(message):
            assert number in source, f"{lead.Lead_ID}: {number} not in row"
        assert lead.Client_Name.split()[0] in message or message.startswith("Not Applicable")


def test_no_currency_conversion(result):
    _, out, _ = result
    for lead_id in ["L006", "L014"]:  # AED leads must stay AED
        assert "AED" in out.loc[lead_id, "Budget"]


def test_review_fixes(result):
    # L019 asked to be called in January 2027 -> message confirms it instead of asking "when"
    assert "January 2027" in row(result, "L019").Suggested_WhatsApp
    assert "When would be a good time" not in row(result, "L019").Suggested_WhatsApp
    # L022 next action does not repeat "exit/rental case"
    assert row(result, "L022").Next_Action.count("exit/rental case") == 1
    # L009 "₹7–8 Cr" -> "Up to ₹8 Cr" is the same ceiling, not a conflict; real changes still flagged
    assert row(result, "L009").Conflict_Flags == "None"
    assert "4 Cr" in row(result, "L002").Conflict_Flags


def test_priority_list_sheet(result):
    import io

    import pandas as pd

    from src.pipeline import to_excel_bytes
    _, out, _ = result
    sheets = pd.read_excel(io.BytesIO(to_excel_bytes(out.reset_index(drop=True))), sheet_name=None)
    assert list(sheets) == ["All_Leads", "Priority_List"]
    prio = sheets["Priority_List"]
    assert len(prio) == 24 and set(prio.Lead_ID) == set(out.Lead_ID)
    assert list(prio.Rank) == list(range(1, 25))
    assert prio.Buying_Probability_0_100.is_monotonic_decreasing
    assert prio.Lead_ID.iloc[0] == "L016"
    assert prio.Score_Breakdown.notna().all()


def test_validator_rejects_invented_evidence():
    from src.extractor import FieldResult
    from src.validator import validate_field
    fake = FieldResult("₹9 Cr", ["Client wants ₹9 Cr"], 90, "latest")
    checked = validate_field(fake, "Budget around ₹4 Cr.")
    assert checked.value == NA and checked.confidence < 90
