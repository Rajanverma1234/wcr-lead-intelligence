"""White Collar Realty - Lead Intelligence (Streamlit UI).

Run:  streamlit run app.py
"""
import pandas as pd
import streamlit as st

from src.extractor import NA
from src.llm_optional import available_providers
from src.loader import DEFAULT_INPUT, OUTPUT_DIR, load_leads
from src.pipeline import priority_list, process_all, save_outputs, to_excel_bytes

st.set_page_config(page_title="WCR Lead Intelligence", page_icon="🏠", layout="wide")
TEMP_COLORS = {"Hot": "#ffd6d6", "Warm": "#fff1cc", "Cold": "#dbeafe"}


def color_rows(row):
    color = TEMP_COLORS.get(row.get("Lead_Temperature"), "")
    return [f"background-color: {color}; color: #111" if color else ""] * len(row)


# ---------------------------------------------------------------- sidebar: input + options
st.sidebar.header("1. Input")
uploaded = st.sidebar.file_uploader("Upload a lead file (.xlsx with a Lead_Data sheet)", type=["xlsx"])
st.sidebar.caption(f"Default: data/{DEFAULT_INPUT.name}")

st.sidebar.header("2. Optional LLM layer")
use_llm = st.sidebar.toggle("Use LLM extraction (off = rule-based only)", value=False)
provider = None
if use_llm:
    provider = st.sidebar.selectbox("Provider", available_providers())
    st.sidebar.caption("LLM values are kept only if their evidence passes the same validator. "
                       "Scoring always stays rule-based. Any error falls back to rules.")

st.title("🏠 White Collar Realty – Lead Intelligence")
st.caption("Rule-based, evidence-backed extraction. Anything not in the remarks = **Not Available**.")

if st.button("Process Leads", type="primary"):
    try:
        leads = load_leads(uploaded if uploaded is not None else None)
        with st.spinner("Processing leads..."):
            out, details = process_all(leads, use_llm=use_llm, llm_provider=provider or "anthropic")
        save_outputs(out, OUTPUT_DIR)
        st.session_state.update(leads=leads, out=out, details=details)
        st.success(f"Processed {len(out)} leads. Saved output/processed_leads.csv and .xlsx")
    except Exception as exc:  # show a friendly error instead of a stack trace
        st.error(f"Could not process the file: {exc}")

if "out" not in st.session_state:
    st.info("Click **Process Leads** to start.")
    st.stop()

leads, out, details = st.session_state["leads"], st.session_state["out"], st.session_state["details"]

# ---------------------------------------------------------------- sidebar filters
st.sidebar.header("3. Filters")
temps = st.sidebar.multiselect("Temperature", ["Hot", "Warm", "Cold"], default=["Hot", "Warm", "Cold"])
markets = sorted(out["Market"].unique())
market_sel = st.sidebar.multiselect("Market", markets, default=markets)
min_prob = st.sidebar.slider("Minimum buying probability", 0, 100, 0, 5)
view = out[out["Lead_Temperature"].isin(temps) & out["Market"].isin(market_sel)
           & (out["Buying_Probability_0_100"] >= min_prob)]

# ---------------------------------------------------------------- summary metrics
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total leads", len(out))
c2.metric("🔥 Hot", int((out["Lead_Temperature"] == "Hot").sum()))
c3.metric("🌤 Warm", int((out["Lead_Temperature"] == "Warm").sum()))
c4.metric("❄ Cold", int((out["Lead_Temperature"] == "Cold").sum()))

# ---------------------------------------------------------------- downloads
d1, d2, d3, _ = st.columns([1, 1, 1.4, 3])
d1.download_button("⬇ CSV", out.to_csv(index=False).encode("utf-8-sig"), "processed_leads.csv", "text/csv")
d2.download_button("⬇ XLSX", to_excel_bytes(out), "processed_leads.xlsx",  # All_Leads + Priority_List sheets
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
d3.download_button("⬇ Priority list CSV", priority_list(out).to_csv(index=False).encode("utf-8-sig"),
                   "priority_list.csv", "text/csv")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["Priority View", "Full Output", "Lead Detail", "Duplicates & Conflicts",
                                        "Before vs After"])

# ---------------------------------------------------------------- 1. priority view
with tab1:
    st.subheader("Who to call first")
    priority = priority_list(view)  # same ranking as the Priority_List sheet in the Excel file
    st.dataframe(priority.style.apply(color_rows, axis=1), width="stretch", hide_index=True)

# ---------------------------------------------------------------- 2. full output
with tab2:
    st.dataframe(view.style.apply(color_rows, axis=1), width="stretch", hide_index=True)

# ---------------------------------------------------------------- 3. lead detail
with tab3:
    lead_id = st.selectbox("Select Lead_ID", out["Lead_ID"].tolist())
    r = out.set_index("Lead_ID").loc[lead_id]
    src = leads.set_index("Lead_ID").loc[lead_id]
    det = details[lead_id]

    st.markdown(f"### {lead_id} – {r['Client_Name']} ({r['Market']})")
    m1, m2, m3 = st.columns(3)
    m1.metric("Temperature", r["Lead_Temperature"])
    m2.metric("Buying probability", r["Buying_Probability_0_100"])
    m3.metric("Confidence", r["Confidence_0_100"])

    left, right = st.columns(2)
    with left:
        st.markdown("**Original remarks**")
        st.markdown(f"*Previous:* {src['Previous_Remarks'] or '—'}")
        st.markdown(f"*Latest:* {src['Latest_Remarks'] or '—'}")
        st.markdown(f"*Recorded:* Project = {src['Recorded_Project'] or '—'}, Budget = {src['Recorded_Budget'] or '—'}, "
                    f"Status = {src['Current_Status']}, Last contact = {src['Last_Contact_Date']}")
    with right:
        st.markdown("**Score breakdown**")
        for line in det["score_breakdown"]:
            st.markdown(f"- {line}")

    st.markdown("**Extracted fields (value · evidence · confidence)**")
    field_rows = []
    for name, f in det["fields"].items():
        field_rows.append({
            "Field": name, "Value": f["value"],
            "Evidence": " | ".join(f["evidence"]) if f["evidence"] else "—",
            "Confidence": f["confidence"], "Source": f.get("source_label", f["source"]),
            "Conflict": " | ".join(det["conflicts"].get(name, [])) or "—",
            "Note": f["note"] or "",
        })
    st.dataframe(pd.DataFrame(field_rows), width="stretch", hide_index=True)

    st.markdown(f"**Next action:** {r['Next_Action']}")
    st.markdown(f"**Suggested WhatsApp:** {r['Suggested_WhatsApp']}")
    if r["Duplicate_Of"] != "None":
        st.warning(f"Possible duplicate of {r['Duplicate_Of']} – {det.get('duplicate_reason', '')}")

# ---------------------------------------------------------------- 4. duplicates & conflicts
with tab4:
    st.subheader("Duplicates")
    dups = out[out["Duplicate_Of"] != "None"][["Lead_ID", "Client_Name", "Duplicate_Of"]].copy()
    dups["Reason"] = dups["Lead_ID"].map(lambda i: details[i].get("duplicate_reason", ""))
    if len(dups):
        st.dataframe(dups, width="stretch", hide_index=True)
    else:
        st.write("No duplicates found.")

    st.subheader("Conflicts (latest remark wins, change is recorded)")
    conf = out[out["Conflict_Flags"] != "None"][["Lead_ID", "Client_Name", "Conflict_Flags"]]
    if len(conf):
        st.dataframe(conf, width="stretch", hide_index=True)
    else:
        st.write("No conflicts found.")

# ---------------------------------------------------------------- 5. before vs after
with tab5:
    from src.compare import build_comparison
    from src.loader import PROJECT_ROOT
    st.caption("Frozen round-1 engine (legacy/engine_v1) vs current engine, both run live on the same leads.")
    st.dataframe(build_comparison(leads, "Current input"), width="stretch", hide_index=True)
    scenarios = PROJECT_ROOT / "data" / "test_scenarios.xlsx"
    if scenarios.exists():
        st.subheader("Test scenarios (data/test_scenarios.xlsx)")
        st.dataframe(build_comparison(load_leads(scenarios), "Test scenarios"), width="stretch", hide_index=True)
