# White Collar Realty – AI Lead Intelligence

A small tool that reads messy real-estate lead remarks and turns them into sales intelligence:
budget, timeline, purpose, projects, location, site-visit status, objections, next action,
Hot/Warm/Cold, buying probability, confidence, evidence and a safe WhatsApp follow-up.

**Core idea:** a deterministic, rule-based pipeline where *every* output is backed by a quote
from the remarks. If something is not in the data, the answer is **Not Available**.

---

## 1. How to run

```bash
# 1. Create and activate a virtual environment (from inside the "AI leads" folder)
python -m venv .venv
.venv\Scripts\activate          # Windows (PowerShell / cmd)
source .venv/bin/activate       # Mac / Linux

# 2. Install
pip install -r requirements.txt

# 3. Process the Excel (no UI). Writes to output/:
#    processed_leads.xlsx  -> sheet "All_Leads" (every field, Lead_ID order)
#                             sheet "Priority_List" (Rank 1..N, highest probability first, with Score_Breakdown)
#    processed_leads.csv, priority_list.csv
python run_pipeline.py

# 4. Web app
streamlit run app.py            # opens http://localhost:8501 -> click "Process Leads"

# 5. Tests (55: 23 round-1 acceptance tests + 32 round-2 correction tests)
pytest -v

# 6. Round 2: before vs after (legacy engine vs current engine)
python scripts/make_test_scenarios.py      # (re)creates data/test_scenarios.xlsx (L900–L902)
python scripts/compare_before_after.py     # writes outputs/before_after.csv and outputs/before_after.md
```

### Round 2 corrections

| Area | What changed | Code |
|---|---|---|
| Timeline | Each remark is classified separately as CONFIRMED / TENTATIVE / VAGUE / UNCERTAIN / LONG_DELAY / NONE (English + common Hinglish). **The latest remark overrides the previous one.** The previous timeline goes to `Timeline_History` + a `TIMELINE_CHANGED` flag and **never adds points** once the latest remark changes it. UNCERTAIN/VAGUE are checked before numbers. | `src/timeline.py` |
| Timeline points | CONFIRMED ≤1 / ≤3 / ≤6 months: +20 / +10 / +5 · TENTATIVE: +10 / +5 / +2 · VAGUE, UNCERTAIN, NONE: 0 · LONG_DELAY: −25. The Score_Breakdown always has an explicit `Timeline:` line. | `src/scoring.py` |
| Dependencies | A **list**, one item per distinct dependency, each with its own evidence clause (property sale, spouse/family approval, home loan, NRI fund transfer, possession, job transfer + round-1 rules). −10 each. New column `Dependencies_List`. | `src/dependencies.py` |
| Conflicts | Typed flags: `TIMELINE_CHANGED`, `BUDGET_CHANGED`, `BUDGET_INCREASED`, `CURRENCY_DIFFERENT`, `PURPOSE_CHANGED`, `PROJECT_CHANGED`, `LOCATION_CHANGED`, `SITE_VISIT_CHANGED`, `CRM_VS_REMARK_MISMATCH`, `DUPLICATE_PHONE`. Budgets are compared as rupee ranges, so a change counts as a conflict only if the ranges don't overlap. AED is never converted unless `AED_TO_INR` is set. | `src/conflicts.py` |
| Evidence | Each field is `{value, source, evidence, confidence}`. The source is `LATEST_REMARK`, `PREVIOUS_REMARK`, `CRM:<column>`, `DERIVED` or `NONE`, and the labels keep **Client remark** and **CRM column** apart. `validate_evidence()` checks that each quote is in **its own** remark and that every number, project, place or date in a value appears in that quote. Negated clauses ("NOT looking to invest") are ignored. Only explicit requests for material score. Location comes only from remarks; Market → `CRM_Market`. Anything that is removed is listed in `Validation_Issues`. | `src/evidence.py`, `src/negation.py` |
| WhatsApp | `validate_whatsapp()` checks that every project, amount, date/window and location in the message is in the validated fields. It also blocks firm wording for an UNCERTAIN/VAGUE timeline, promises (discounts, prices, availability), other leads' names and unacknowledged dependencies. On failure the message is rebuilt from a safe template. Result in `WhatsApp_Validation` (PASS / FIXED + reasons). | `src/whatsapp_validator.py` |
| Before vs After | `legacy/engine_v1` is a frozen copy of the round-1 engine. The comparison runs both engines live (CLI script, `/before-after` page on the web app, and a Streamlit tab). | `src/compare.py` |

New output columns (all existing columns kept): `Timeline_Category`, `Timeline_History`, `Dependencies_List`,
`CRM_Market`, `Field_Sources`, `Validation_Issues`, `WhatsApp_Validation`.
Scenario L900 (latest remark: "timeline uncertain", property sale + wife approval) drops from 45 Warm to **25 Cold**.
Scenario L901 (tentative 2–3 months, home loan) drops from 80 Hot to **55 Warm**.
The original workbook is never modified; a test checks its SHA-256.

No API key is needed. The optional LLM layer is off by default (see section 8).

### Live web version (Vercel)

**https://wcr-lead-intelligence.vercel.app**

Streamlit needs a long-running server, which Vercel's serverless functions don't provide, so the hosted
version is `api/index.py`: a small FastAPI page that runs the **same `src/` pipeline**. It has the Priority View,
Full Output, Lead Detail (evidence + score breakdown), Duplicates & Conflicts, filters, CSV/XLSX download
and Excel upload. Deployed directly from this folder with the Vercel CLI (no GitHub):

```bash
vercel deploy --prod      # uses vercel.json + pyproject.toml (lean deps); .vercelignore keeps the bundle small
uvicorn api.index:app --reload   # run the web version locally (pip install fastapi python-multipart uvicorn)
```

---

## 2. Architecture

```
data/WCR_..._Candidate_Pack.xlsx  (Lead_Data sheet)
        │
        ▼
 loader.py      read as text, trim, normalize phone (last 10 digits)
        │
        ▼
 extractor.py   split remarks into sentences → one function per field
                LATEST remarks first → PREVIOUS remarks → Recorded_* column → "Not Available"
                each field = (value, evidence quotes, confidence, source)
        │
        ▼
 validator.py   evidence must be a substring of the row; numbers in value must be in evidence
                → otherwise value becomes "Not Available"
        │
        ├──► conflicts.py   previous vs latest (and Recorded_Budget) → Conflict_Flags
        ├──► duplicates.py  same phone / similar name → Duplicate_Of
        ├──► scoring.py     base 40 ± rules → Buying_Probability + Hot/Warm/Cold + Score_Breakdown
        └──► messaging.py   Next_Action + WhatsApp template → validated again
        │
        ▼
 pipeline.py    process_lead(row) / process_all(df) → output DataFrame
        │
        ├──► run_pipeline.py  (CLI)  → output/processed_leads.csv / .xlsx
        └──► app.py           (Streamlit UI: Priority View, Full Output, Lead Detail, Duplicates & Conflicts)

 llm_optional.py  (OFF by default) LLM proposes values → SAME validator → else rule value is kept
```

---

## 3. How Buying Probability is calculated

Start at **40**, add/subtract every rule that fires, clamp to 0–100. All weights live in one
dictionary (`WEIGHTS` at the top of `src/scoring.py`), so they are easy to explain and tune.

| Rule | Points |
|---|---|
| Negotiating / asked for final offer | **+30** |
| Site visit completed or confirmed | **+25** |
| Site visit tentative | +10 |
| Timeline CONFIRMED ≤ 1 / ≤ 3 / ≤ 6 months | +20 / +10 / +5 |
| Timeline TENTATIVE ≤ 1 / ≤ 3 / ≤ 6 months | +10 / +5 / +2 |
| Timeline VAGUE / UNCERTAIN / not mentioned | 0 |
| Clear budget stated in remarks | +5 |
| Budget increased vs previous remark | +5 |
| Specific requirement (BHK, number of units, specific project) | +5 |
| Actively requested material (payment plan, floor plan, inventory, comparison, options…) | +5 |
| Each dependency (family decision, sell property, waiting for bonus/flight…) | −10 |
| Budget mismatch / cannot stretch | −20 |
| Timeline LONG_DELAY (> 6 months, or "not before / not for" ≥ 6 months) | −25 |
| Not interested / asked not to follow up / no plan to buy | −40 |
| No requirement captured at all (budget, timeline, purpose, project all missing) | −20 |

**Temperature:** Hot ≥ 70 · Warm 40–69 · Cold < 40.
**Override:** if the client said not interested / do not follow up / no investment for 12+ months → always **Cold**.

Timelines are measured from the lead's `Last_Contact_Date` (because "within 2 months" was said on that date).
They are only used for scoring and are **never shown as a fake date**.

### Worked example – L002 (Neha Kapoor) → 85, Hot

Latest remark: *"Budget has increased. Maximum ₹6.5 Cr now. Wants a 5 BHK for end use. She confirmed she can
visit on Sunday at 12 PM with her husband. Send location and floor plan today."*

| Step | Points | Running total |
|---|---|---|
| Base | 40 | 40 |
| Timeline: NONE – no purchase timeline in either remark | 0 | 40 |
| Site visit confirmed ("She confirmed she can visit on Sunday at 12 PM") | +25 | 65 |
| Clear budget stated (Up to ₹6.5 Cr) | +5 | 70 |
| Budget increased (₹4 Cr → ₹6.5 Cr) | +5 | 75 |
| Specific requirement (5 BHK) | +5 | 80 |
| Requested material (location, floor plan) | +5 | 85 |
| **Final** | | **85 → Hot** |

The exact same breakdown is shown in the `Score_Breakdown` column and in the app's Lead Detail tab.

---

## 4. How hallucination is prevented

1. **"Not Available" rule** – every extractor returns `Not Available` unless a regex rule matched an actual
   sentence. No defaults, no guessing (e.g. L005 has Budget/Purpose/Timeline/Projects = Not Available).
2. **Evidence substring validation** – evidence is the exact sentence copied from the remarks (or a labelled
   column such as `Recorded_Project: M3M BRABUS`). `validator.py` checks it is a case-insensitive substring of
   the row and that every number in the value appears in the evidence. If not → `Not Available` + lower confidence.
   A pytest test checks this for every field of every lead.
3. **No currency conversion** – `₹` stays `₹`, `AED` stays `AED`. L014 is "Around AED 2M"; the CRM's
   "₹5 Cr equivalent" is only shown as a conflict, never converted.
4. **No fake dates** – the Timeline column shows the client's own words with the category, e.g.
   *"Vague: sometime next year"*, *"Tentative: maybe within 3–4 months"*. A month/year is shown only if it is
   written in the text (December 2027). `months_estimate` is used only for scoring.
5. **Template-based WhatsApp + validation** – messages are filled from validated fields only, then checked by
   `validate_whatsapp()` (round 2, see above). On failure a safe template is used and `WhatsApp_Validation`
   says why. Leads who asked not to be contacted get *"Not Applicable – client asked not to be contacted for now"*.
6. **LLM output is re-validated** – the optional LLM never bypasses the validator, and scoring is always rule-based.

**Confidence (0–100)** = average of field confidences: 90 explicit in latest remark · 75 only in previous remark ·
60 only in a CRM column · 40 if the validator removed a value · 100 for
"Not Available" when nothing exists (we are sure it is absent) · −10 if that field has a conflict.

---

## 5. Conflict handling

Latest remark wins, but the old value is never lost. The same finder function is run separately on
previous and latest remarks (and on the CRM columns), and differences go to `Conflict_Flags` as typed flags:

- L002: `BUDGET_INCREASED: "Around ₹4 Cr" (previous remark) → "Up to ₹6.5 Cr" (latest remark)` and
  `CRM_VS_REMARK_MISMATCH: CRM Recorded_Budget "₹4 Cr" vs "Up to ₹6.5 Cr" (latest remark)`
- L014: `CURRENCY_DIFFERENT: CRM Recorded_Budget "₹5 Cr equivalent" vs "Around AED 2M" (latest remark) – not converted`
- L020: `TIMELINE_CHANGED: "this quarter" (previous remark) → "no investment for at least 12 months" (latest remark)`
- L009: no flag – "₹7–8 Cr" and "up to ₹8 Cr" overlap, so the budget did not really change.

## 6. Duplicate detection

Phones are normalized (digits only, last 10 digits → removes +91, spaces, dashes, leading 0).
Same phone = duplicate. Fuzzy name similarity (difflib ratio > 0.85) is a supporting signal, and a
name-only match is reported as a *possible* duplicate. L021 "Samir Khan" (9810011005) = L005 "Sameer Khan"
(09810011005) → `Duplicate_Of = L005`, and the next action says to merge records before calling.

## 7. Limitations

- Regex rules are written for the phrasing in this dataset; new phrasing needs new rules (or the LLM layer).
- Known project/location names are a fixed list (`KNOWN_PROJECTS`, `KNOWN_LOCATIONS` in `extractor.py`).
- Scoring weights are expert guesses, not trained on real conversion data – it is an explainable ranking, not a
  scientific probability.
- "Tomorrow" / "next Sunday" are kept as written (relative to the last contact date), not resolved to dates.
- Location falls back to the Market column with lower confidence and is clearly labelled "(from Market column)".

## 8. Optional LLM layer (`src/llm_optional.py`)

Off by default (sidebar toggle). Keys come from environment / `.env` (copy `.env.example`), never from code.

- **Anthropic** (`ANTHROPIC_API_KEY`), model `claude-haiku-5-5`. Note: this model rejects a non-default
  `temperature`, so instead we use low effort + a strict "copy exact evidence, return only JSON" instruction.
- **OpenAI** (`OPENAI_API_KEY`) – temperature 0, JSON mode.
- **Ollama** local model at `http://localhost:11434` – temperature 0, JSON format.

The prompt contains only that one row and asks for `{field: {value, evidence}}`, with "return Not Available if not
stated". Every LLM value passes through the same validator; on any failure or error the rule-based value is used.

## 9. Scaling to 25,000+ leads

- **Batch processing** – read the file in chunks; the rule engine is pure Python and processes thousands of rows
  per second, so 25k leads take well under a minute on a laptop.
- **Vectorized pandas** for cleaning (phone normalization, column trimming) and duplicate grouping
  (`groupby` on normalized phone instead of the O(n²) pairwise loop; blocking by first letter / phone prefix for fuzzy names).
- **Caching** – hash each row's remarks; only re-process leads whose remarks changed since the last run.
- **Async LLM calls with rate limits** – only for leads the rules can't handle (low confidence), using a
  semaphore + retry/backoff, or a batch API for overnight runs at lower cost.
- **Database** – store results (Postgres/SQLite) keyed by Lead_ID + remark hash, with history of scores.
- **Human review queue** – leads with low confidence, conflicts or validator rejections go to a sales-ops reviewer.
- **Monitoring accuracy** – keep a labelled sample (e.g. 200 leads), re-score it on every rule change and track
  field accuracy and Hot-lead precision over time.

---

## 10. Interview talking points (3–5 minutes)

1. **Problem (30s).** Sales teams have thousands of leads with messy free-text remarks. They need to know who to
   call first and what to say – without the tool inventing facts, because a wrong budget or a fake visit date in a
   WhatsApp message damages trust with a high-value client.

2. **Approach (60s).** I built a rule-based pipeline as the core because it is deterministic, fast, free and
   fully explainable. Each remark is split into sentences; for each field I have a small function with regex rules.
   The latest remark is checked first, then the previous one, then CRM columns. Every value carries the exact
   sentence it came from as evidence.

3. **Anti-hallucination (60s).** Three layers: (1) if no rule matches, the answer is "Not Available";
   (2) a validator proves every evidence quote exists in the row and every number in a value exists in its evidence –
   otherwise the value is dropped; (3) WhatsApp messages are templates filled from extracted facts and re-checked:
   any number, currency, date or project not in the row → fall back to a generic message. I never convert currencies
   (AED stays AED) and never turn "sometime next year" into a date. There's a pytest test that checks evidence for
   every lead.

4. **Probability (60s).** Base 40, then transparent points: +30 negotiation, +25 confirmed/completed visit,
   +20 buying within 30 days, −10 per dependency, −40 not interested, etc. All weights are in one dictionary.
   Example: L002 is 40 + 25 confirmed visit + 5 budget + 5 budget increase + 5 for 5 BHK + 5 asked for floor plan = 85, Hot.
   L016, who completed the visit and is negotiating, scores 100. Confidence is separate – it measures how sure the
   extraction is, not how likely the sale is.

5. **Conflicts & duplicates (30s).** Latest remark wins but the change is logged, e.g. budget ₹4 Cr → ₹6.5 Cr,
   or "this quarter" → "not for 12 months". Duplicates are found by normalized phone – L021 and L005 are the same number.

6. **LLM & scale (30s).** An optional LLM layer (Claude Haiku, OpenAI or local Ollama) is off by default; its output
   must pass the same validator, and scoring stays rule-based. To scale to 25k+ leads: chunked processing, caching by
   remark hash, a database, LLM only for low-confidence leads with rate limits, and a human review queue.

7. **Testing.** 21 pytest acceptance tests, plus I manually checked the output of all 24 leads against the remarks
   and fixed two issues I found (e.g. the "3" in "M3M" was wrongly treated as an unsupported number by the validator).
