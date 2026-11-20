"""Round 2 corrections - behaviour tests (never compared against stored full outputs).

Run:  pytest -v tests/test_corrections.py
"""
import hashlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacy.engine_v1.pipeline import process_all as legacy_process_all  # noqa: E402
from src.evidence import validate_evidence  # noqa: E402
from src.extractor import NA, REMARK_COLUMN, REMARK_SOURCES, extract_all  # noqa: E402
from src.loader import DEFAULT_INPUT, load_leads  # noqa: E402
from src.pipeline import process_all  # noqa: E402
from src.scoring import timeline_points  # noqa: E402
from src.timeline import classify_remark  # noqa: E402
from src.whatsapp_validator import fix_whatsapp, validate_whatsapp  # noqa: E402

SCENARIOS = ROOT / "data" / "test_scenarios.xlsx"
ENGINE_FILES = sorted((ROOT / "src").glob("*.py"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def original_hash():
    return sha256(DEFAULT_INPUT)  # taken BEFORE any engine run in this module


@pytest.fixture(scope="module")
def originals(original_hash):
    leads = load_leads()
    out, details = process_all(leads)
    return leads, out.set_index("Lead_ID", drop=False), details


@pytest.fixture(scope="module")
def scenarios():
    if not SCENARIOS.exists():
        from scripts.make_test_scenarios import main
        main()
    leads = load_leads(SCENARIOS)
    out, details = process_all(leads)
    legacy, _ = legacy_process_all(leads)
    return leads, out.set_index("Lead_ID", drop=False), details, legacy.set_index("Lead_ID")


def timeline_line(row) -> str:
    return next(line for line in row.Score_Breakdown.split(" | ") if line.startswith("Timeline:"))


# ------------------------------------------------------------------ 1. timeline classifier
@pytest.mark.parametrize("text, category", [
    ("Client wants to buy within 2 months.", "CONFIRMED"),
    ("Closing next week.", "CONFIRMED"),
    ("2 mahine mein lena hai.", "CONFIRMED"),
    ("Probably will close in 2-3 months.", "TENTATIVE"),
    ("Maybe around 2-3 months, if all goes well.", "TENTATIVE"),
    ("Client says he will buy sometime next year.", "VAGUE"),
    ("Will buy in future, agle saal dekhenge.", "VAGUE"),
    ("Purchase timeline is currently uncertain.", "UNCERTAIN"),
    ("Deal on hold, pakka nahi.", "UNCERTAIN"),
    ("Uncertain, but maybe within 1 month.", "UNCERTAIN"),           # uncertain beats a number
    ("Purchase postponed to 2028.", "LONG_DELAY"),
    ("Will buy after 1 year.", "LONG_DELAY"),
    ("Next to next year.", "LONG_DELAY"),
    ("Client interested, no timeline shared.", "NONE"),
    ("Asked for a call next week.", "NONE"),                          # a call-back is not a purchase timeline
    ("Not sure about location yet.", "NONE"),                         # doubt about something else
])
def test_timeline_classifier(text, category):
    result = classify_remark(text)
    assert result.category == category, (text, result)
    if category != "NONE":
        assert result.raw_text and result.raw_text.lower() in text.lower()  # exact words, nothing invented


def test_sometime_next_year_is_vague_and_zero_points():
    result = classify_remark("Client says he will buy sometime next year.")
    assert result.category == "VAGUE" and result.months_estimate is None
    assert timeline_points(result.category, result.months_estimate) == 0


def test_timeline_points_table():
    assert timeline_points("CONFIRMED", 1) == 20 and timeline_points("CONFIRMED", 3) == 10
    assert timeline_points("CONFIRMED", 6) == 5
    assert timeline_points("TENTATIVE", 1) == 10 and timeline_points("TENTATIVE", 3) == 5
    assert timeline_points("TENTATIVE", 6) == 2
    for cat in ("VAGUE", "UNCERTAIN", "NONE"):
        assert timeline_points(cat, None) == 0
    assert timeline_points("LONG_DELAY", 12) == -25


# ------------------------------------------------------------------ 2. scenario L900
def test_L900_latest_overrides_previous(scenarios):
    _, out, details, legacy = scenarios
    r = out.loc["L900"]
    assert r.Timeline_Category == "UNCERTAIN"
    line = timeline_line(r)
    assert "UNCERTAIN" in line and "→ 0 pts" in line and "ignored for scoring" in line
    assert not re.search(r"[+-]\d+ timeline", r.Score_Breakdown, re.IGNORECASE)  # no timeline points at all
    assert "TIMELINE_CHANGED" in r.Conflict_Flags and "2 months" in r.Conflict_Flags
    assert "2 months" in r.Timeline_History
    # budget from the previous remark is kept, labelled PREVIOUS_REMARK
    budget = details["L900"]["fields"]["Budget"]
    assert budget["value"] == "₹5 Cr" and budget["source"] == "PREVIOUS_REMARK"
    # exactly two dependencies: property sale + wife approval
    deps = [d for d in details["L900"]["dependencies"] if d["kind"] == "dependency"]
    assert [d["type"] for d in deps] == ["PROPERTY_SALE", "FAMILY_APPROVAL"]
    assert "wife" in deps[1]["evidence"].lower() and "selling" in deps[0]["evidence"].lower()
    assert r.Score_Breakdown.count("dependency:") == 2
    # corrected score is lower than the round-1 engine's
    assert r.Buying_Probability_0_100 < legacy.loc["L900", "Buying_Probability_0_100"]


# ------------------------------------------------------------------ 3. scenario L901
def test_L901_tentative_budget_increase_loan(scenarios):
    _, out, details, legacy = scenarios
    r = out.loc["L901"]
    assert r.Timeline_Category == "TENTATIVE"
    assert "+5 pts" in timeline_line(r)                               # reduced from the confirmed +20
    assert "previous 'within 1 month' ignored" in timeline_line(r)
    assert "BUDGET_INCREASED" in r.Conflict_Flags
    deps = [d for d in details["L901"]["dependencies"] if d["kind"] == "dependency"]
    assert [d["type"] for d in deps] == ["HOME_LOAN"]
    assert "+5 actively requested material: brochure, floor plan" in r.Score_Breakdown
    assert r.Buying_Probability_0_100 < legacy.loc["L901", "Buying_Probability_0_100"]


# ------------------------------------------------------------------ 4. scenario L902
def test_L902_vague_and_nothing_invented(scenarios):
    _, out, _, _ = scenarios
    r = out.loc["L902"]
    assert r.Timeline_Category == "VAGUE"
    assert timeline_line(r).endswith("→ 0 pts")
    assert r.Location_Preference == NA        # "not sure about location yet" + blank Market column
    assert r.CRM_Market == NA
    assert r.Budget == NA and r.Projects_Discussed == NA


# ------------------------------------------------------------------ 5. original lead fixes
def test_L003_negation_and_casual_question(originals):
    _, out, _ = originals
    r = out.loc["L003"]
    assert r.Purpose == NA                    # "NOT looking to invest" is not an investment purpose
    assert "requested material" not in r.Score_Breakdown
    assert "casually" not in r.Score_Breakdown         # the casual question earns no points of any kind
    assert "negated" in r.Validation_Issues and "casual question not counted" in r.Validation_Issues


def test_L009_no_budget_conflict(originals):
    _, out, _ = originals
    assert not re.search(r"BUDGET_|CURRENCY_DIFFERENT|CRM_VS_REMARK", out.loc["L009"].Conflict_Flags)


def test_market_is_never_a_location_preference(originals):
    _, out, details = originals
    for lead_id, d in details.items():
        assert d["fields"]["Location_Preference"]["source"] in (*REMARK_SOURCES, "NONE"), lead_id
        assert "Market column" not in out.loc[lead_id].Location_Preference


# ------------------------------------------------------------------ 6. evidence
def _all_results(originals, scenarios):
    for leads, out, details in (originals, scenarios[:3]):
        for _, lead in leads.iterrows():
            yield lead, out.loc[lead.Lead_ID], details[lead.Lead_ID]


def test_remark_evidence_is_substring_of_its_own_remark(originals, scenarios):
    norm = lambda t: re.sub(r"\s+", " ", t).strip().lower()  # noqa: E731
    for lead, _, det in _all_results(originals, scenarios):
        for name, f in det["fields"].items():
            if f["value"] == NA or f["source"] not in REMARK_SOURCES:
                continue
            remark = norm(lead[REMARK_COLUMN[f["source"]]])
            for quote in f["evidence"]:
                assert norm(quote) in remark, (lead.Lead_ID, name, quote, f["source"])


def test_validator_rejects_evidence_from_the_wrong_remark():
    lead = {"Lead_ID": "X", "Latest_Remarks": "Budget up to ₹8 Cr.", "Previous_Remarks": "Budget ₹5 Cr.",
            "Recorded_Budget": "", "Recorded_Project": "", "Market": "", "Last_Contact_Date": ""}
    fields = extract_all(lead)
    fields["Budget"].evidence = ["Budget ₹5 Cr."]            # quote exists in the row, but in the OTHER remark
    validated, issues = validate_evidence(lead, fields)
    assert validated["Budget"].value == NA and validated["Budget"].source == "NONE"
    assert any("evidence not found in Latest_Remarks" in i for i in issues)


def test_validator_rejects_number_not_in_evidence():
    lead = {"Lead_ID": "X", "Latest_Remarks": "Budget up to ₹8 Cr.", "Previous_Remarks": "",
            "Recorded_Budget": "", "Recorded_Project": "", "Market": "", "Last_Contact_Date": ""}
    fields = extract_all(lead)
    fields["Budget"].value = "Up to ₹9 Cr"
    validated, issues = validate_evidence(lead, fields)
    assert validated["Budget"].value == NA and any('"9"' in i for i in issues)


# ------------------------------------------------------------------ 7. WhatsApp
def test_whatsapp_injected_fake_facts_fail_and_get_fixed(scenarios):
    leads, _, _, _ = scenarios
    lead = dict(leads.set_index("Lead_ID", drop=False).loc["L900"])
    fields, _ = validate_evidence(lead, extract_all(lead))
    fake = "Hi Aarav, DLF Privana is available at ₹9 Cr with a special discount if you book within 2 months."
    reasons = validate_whatsapp(fake, fields)
    assert any("DLF Privana" in r for r in reasons)
    assert any("9" in r for r in reasons)
    assert any("UNCERTAIN timeline as firm" in r for r in reasons)
    assert any("discount" in r for r in reasons)
    fixed, status = fix_whatsapp(fake, "Aarav", fields, [], [])
    assert status.startswith("FIXED")
    assert validate_whatsapp(fixed, fields) == []
    assert "Privana" not in fixed and "9" not in fixed and "Aarav" in fixed


def test_every_generated_whatsapp_passes_validation(originals, scenarios):
    for _, r, _ in _all_results(originals, scenarios):
        assert r.WhatsApp_Validation.startswith(("PASS", "FIXED")), r.Lead_ID
    assert scenarios[1].loc["L900"].WhatsApp_Validation == "PASS"   # dependencies acknowledged in the draft
    assert "no rush" in scenarios[1].loc["L900"].Suggested_WhatsApp.lower()


def test_whatsapp_mentioning_another_lead_fails(scenarios):
    leads, _, _, _ = scenarios
    lead = dict(leads.set_index("Lead_ID", drop=False).loc["L902"])
    fields, _ = validate_evidence(lead, extract_all(lead))
    msg = "Hi Kabir, Diya Test also bought here. Please let me know if you would like any more details."
    assert any("another lead" in r for r in validate_whatsapp(msg, fields, other_names=["Diya Test"],
                                                              client_name="Kabir Test"))


# ------------------------------------------------------------------ 8. round 2b corrections
def synthetic_lead(previous: str, latest: str, **extra) -> dict:
    lead = {"Lead_ID": "T001", "Client_Name": "Test Person", "Phone": "", "Market": "", "Current_Status": "",
            "Last_Contact_Date": "2026-08-17", "Recorded_Project": "", "Recorded_Budget": "",
            "Previous_Remarks": previous, "Latest_Remarks": latest}
    lead.update(extra)
    return lead


def test_budget_ceiling_increase_scores_without_conflict():
    from src.pipeline import process_lead
    # overlapping ranges, higher ceiling: +5, but no budget conflict flag
    out, _ = process_lead(synthetic_lead("Earlier said budget around ₹5 Cr.", "Now looking at ₹5–6 Cr."))
    assert "+5 budget increased" in out["Score_Breakdown"]
    assert not re.search(r"BUDGET_|CURRENCY_DIFFERENT", out["Conflict_Flags"])
    # same ceiling ("₹7–8 Cr" -> "up to ₹8 Cr"): no increase, no conflict
    out, _ = process_lead(synthetic_lead("Budget ₹7–8 Cr.", "Budget up to ₹8 Cr."))
    assert "budget increased" not in out["Score_Breakdown"] and out["Conflict_Flags"] == "None"
    # non-overlapping increase: +5 AND a BUDGET_INCREASED conflict flag
    out, _ = process_lead(synthetic_lead("Budget around ₹4 Cr.", "Maximum ₹6.5 Cr now."))
    assert "+5 budget increased" in out["Score_Breakdown"] and "BUDGET_INCREASED" in out["Conflict_Flags"]
    # "around X" uses the stated X (not X+10%): around 5 -> up to 5.4 is an increase in ceiling
    out, _ = process_lead(synthetic_lead("Budget around ₹5 Cr.", "Budget up to ₹5.4 Cr."))
    assert "+5 budget increased" in out["Score_Breakdown"]


def test_original_budget_leads_after_ceiling_rule(originals):
    _, out, _ = originals
    assert "+5 budget increased" in out.loc["L001"].Score_Breakdown
    assert not re.search(r"BUDGET_", out.loc["L001"].Conflict_Flags)
    assert "BUDGET_INCREASED" in out.loc["L002"].Conflict_Flags
    assert "budget increased" not in out.loc["L009"].Score_Breakdown and out.loc["L009"].Conflict_Flags == "None"


@pytest.mark.parametrize("text, category, points", [
    ("Client wants to buy within 6 months.", "CONFIRMED", 5),        # exactly 6 = not a long delay
    ("Probably will buy in 6 months.", "TENTATIVE", 2),
    ("Wants to buy in 7 months.", "LONG_DELAY", -25),               # strictly more than 6
    ("Not interested. Do not follow up for the next 6 months.", "VAGUE", 0),   # 6-month pause, no window
    ("Do not follow up for the next 8 months.", "LONG_DELAY", -25),
])
def test_long_delay_is_strictly_more_than_six_months(text, category, points):
    result = classify_remark(text)
    assert result.category == category, result
    assert timeline_points(result.category, result.months_estimate) == points


def test_L008_still_zero_and_cold(originals):
    _, out, _ = originals
    r = out.loc["L008"]
    assert r.Timeline_Category == "VAGUE"
    assert r.Buying_Probability_0_100 == 0 and r.Lead_Temperature == "Cold"
    assert "not interested / no follow-up" in r.Score_Breakdown
    assert r.Suggested_WhatsApp.startswith("Not Applicable")


def test_reference_date_is_last_contact_date():
    from src.timeline import resolve_timeline
    remark = "Client will buy by March 2027."
    early = resolve_timeline(synthetic_lead("", remark, Last_Contact_Date="2026-08-17"))["effective"]
    late = resolve_timeline(synthetic_lead("", remark, Last_Contact_Date="2026-12-01"))["effective"]
    assert (early.category, early.months_estimate) == ("LONG_DELAY", 7)   # Aug 2026 -> Mar 2027
    assert (late.category, late.months_estimate) == ("CONFIRMED", 3)      # Dec 2026 -> Mar 2027
    no_date = resolve_timeline(synthetic_lead("", remark, Last_Contact_Date=""))["effective"]
    assert no_date.months_estimate is None and timeline_points(no_date.category, None) == 0
    for path in ENGINE_FILES:   # the engine never reads the clock
        assert not re.search(r"\b(?:date|datetime)\.today\(|\.now\(", path.read_text(encoding="utf-8")), path.name


def test_before_after_written_to_output_folder():
    from scripts.compare_before_after import OUT_DIR
    assert OUT_DIR == ROOT / "output"
    assert not (ROOT / "outputs").exists()


# ------------------------------------------------------------------ 9. anti-hard-coding
def test_no_lead_id_literals_in_engine_source():
    assert ENGINE_FILES
    for path in ENGINE_FILES:
        text = path.read_text(encoding="utf-8")
        hits = re.findall(r"\bL\d{3}\b", text)
        assert not hits, f"{path.name} contains Lead_ID literal(s): {hits}"


ORIGINAL_SHA256 = "ac42f8ec669b5860441d60a0ed80cc9e7eddcea67d2c4da8c07cfe0656ef3f9a"  # the file as received


def test_original_workbook_unchanged_after_runs(original_hash, originals, scenarios):
    # both engines have run (fixtures) - the original file must be byte-identical
    assert sha256(DEFAULT_INPUT) == original_hash == ORIGINAL_SHA256
