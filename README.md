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

# 3. Process the Excel and write output/processed_leads.csv + .xlsx (no UI)
python run_pipeline.py

# 4. Web app
streamlit run app.py            # opens http://localhost:8501 -> click "Process Leads"

# 5. Tests (21 acceptance tests)
pytest -v
```

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
| Timeline ≤ 30 days / "this month" | +20 |
| Timeline ≤ 3 months | +10 |
| Timeline ≤ 6 months | +5 |
| Clear budget stated in remarks | +5 |
| Budget increased vs previous remark | +5 |
| Specific requirement (BHK, number of units, specific project) | +5 |
| Actively requested material (payment plan, floor plan, inventory, comparison, options…) | +5 |
| Each dependency (family decision, sell property, waiting for bonus/flight…) | −10 |
| Budget mismatch / cannot stretch | −20 |
| Timeline > 6 months, or "not before / not for" ≥ 6 months | −25 |
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
4. **No fake dates** – "sometime next year" → *"Sometime next year (no month given)"*; "maybe within 3–4 months"
   → *"Within 3–4 months (tentative)"*. A month/year is shown only if it is written in the text (December 2027).
5. **Template-based WhatsApp + validation** – messages are filled from extracted fields only. Then every number,
   ₹/AED, month, weekday, project and location in the message must exist in the row, otherwise a generic,
   fact-free message is used. Leads who asked not to be contacted get *"Not Applicable – client asked not to be
   contacted for now"*.
6. **LLM output is re-validated** – the optional LLM never bypasses the validator, and scoring is always rule-based.

**Confidence (0–100)** = average of field confidences: 90 explicit in latest remark · 75 only in previous remark ·
60 only in a structured column · 50 inferred (e.g. Market column used as location fallback) · 100 for
"Not Available" when nothing exists (we are sure it is absent) · −10 if that field has a conflict.

---

## 5. Conflict handling

Latest remark wins, but the old value is never lost. The same finder function is run separately on
previous and latest remarks (and on `Recorded_Budget`) and differences go to `Conflict_Flags`:

- L002: `Budget changed: Around ₹4 Cr (previous remark, Recorded_Budget) → Up to ₹6.5 Cr (latest)`
- L014: `Budget changed: ₹5 Cr equivalent (Recorded_Budget) → Around AED 2M (latest)`
- L020: `Timeline changed: This quarter (previous) → No purchase for at least 12 months (latest)`

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
