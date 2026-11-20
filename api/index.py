"""Vercel web version of the app (FastAPI, stateless).

Streamlit needs a long-running server, which Vercel's serverless functions don't
provide, so this file serves the SAME pipeline (src/) as a plain HTML dashboard:
Priority View, Full Output, Lead Detail, Duplicates & Conflicts, filters,
CSV/XLSX downloads and Excel upload. Nothing is stored server-side.

Local run:  uvicorn api.index:app --reload
"""
from __future__ import annotations

import base64
import html
import io
import sys
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # make "src" importable

from src.loader import load_leads  # noqa: E402
from src.pipeline import priority_list, process_all, to_excel_bytes  # noqa: E402
from src.scoring import WEIGHTS  # noqa: E402

app = FastAPI(title="WCR Lead Intelligence")
_DEFAULT_CACHE: dict = {}


def default_results():
    """Process the bundled Excel once per warm instance (24 rows -> milliseconds)."""
    if not _DEFAULT_CACHE:
        leads = load_leads()
        out, details = process_all(leads)
        _DEFAULT_CACHE.update(leads=leads, out=out, details=details)
    return _DEFAULT_CACHE["leads"], _DEFAULT_CACHE["out"], _DEFAULT_CACHE["details"]


def e(value) -> str:
    return html.escape(str(value))


# ---------------------------------------------------------------- HTML rendering
CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1a1d23;--muted:#5f6673;--border:#e3e6ea;--accent:#1f4fd1;
--hot:#fde2e2;--hot-t:#9b1c1c;--warm:#fff3d1;--warm-t:#8a5a00;--cold:#e0ecff;--cold-t:#1e4b8f}
@media (prefers-color-scheme:dark){:root{--bg:#111318;--card:#1a1d24;--text:#e8eaee;--muted:#9aa1ad;
--border:#2c313b;--accent:#7aa2ff;--hot:#3b1d1f;--hot-t:#ffb4b4;--warm:#3a2f14;--warm-t:#ffd780;
--cold:#172a45;--cold-t:#a9c8ff}}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
background:var(--bg);color:var(--text)}
.wrap{max-width:1280px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 10px}.muted{color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:16px 0}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.card b{display:block;font-size:24px}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;background:var(--card);
border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:10px}
.bar label{display:flex;flex-direction:column;font-size:12px;color:var(--muted);gap:3px}
select,input,button{font:inherit;padding:6px 8px;border:1px solid var(--border);border-radius:6px;
background:var(--bg);color:var(--text)}
button,.btn{background:var(--accent);color:#fff;border:0;cursor:pointer;text-decoration:none;
padding:7px 12px;border-radius:6px;display:inline-block}
nav{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}nav a{color:var(--accent);text-decoration:none;
border:1px solid var(--border);background:var(--card);padding:6px 10px;border-radius:999px}
.tbl{overflow-x:auto;background:var(--card);border:1px solid var(--border);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:7px 9px;border-bottom:1px solid var(--border);
text-align:left;vertical-align:top}th{position:sticky;top:0;background:var(--card);white-space:nowrap}
tr.Hot td{background:var(--hot)}tr.Warm td{background:var(--warm)}tr.Cold td{background:var(--cold)}
.pill{padding:1px 8px;border-radius:999px;font-weight:600;font-size:12px}
.pill.Hot{background:var(--hot);color:var(--hot-t)}.pill.Warm{background:var(--warm);color:var(--warm-t)}
.pill.Cold{background:var(--cold);color:var(--cold-t)}
details{background:var(--card);border:1px solid var(--border);border-radius:10px;margin:8px 0;padding:10px 14px}
summary{cursor:pointer;font-weight:600}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:760px){.grid2{grid-template-columns:1fr}}
ul{margin:4px 0;padding-left:18px}.wide{min-width:1800px}
"""


def _row_table(df: pd.DataFrame, cols: list[str], wide: bool = False) -> str:
    head = "".join(f"<th>{e(c)}</th>" for c in cols)
    body = []
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if c == "Lead_Temperature":
                cells.append(f'<td><span class="pill {e(v)}">{e(v)}</span></td>')
            elif c == "Lead_ID":
                cells.append(f'<td><a href="#lead-{e(v)}">{e(v)}</a></td>')
            else:
                cells.append(f"<td>{e(v)}</td>")
        body.append(f'<tr class="{e(r.get("Lead_Temperature", ""))}">{"".join(cells)}</tr>')
    return f'<div class="tbl"><table class="{"wide" if wide else ""}"><tr>{head}</tr>{"".join(body)}</table></div>'


def _lead_detail(lead_id, r, src, det) -> str:
    rows = []
    for name, f in det["fields"].items():
        conflict = " | ".join(det["conflicts"].get(name, [])) or "—"
        evidence = " | ".join(f["evidence"]) or "—"
        rows.append(f"<tr><td>{e(name)}</td><td><b>{e(f['value'])}</b></td><td>{e(evidence)}</td>"
                    f"<td>{e(f['confidence'])}</td><td>{e(f.get('source_label', f['source']))}</td>"
                    f"<td>{e(conflict)}</td></tr>")
    breakdown = "".join(f"<li>{e(line)}</li>" for line in det["score_breakdown"])
    dup = f"<p><b>Possible duplicate of {e(r['Duplicate_Of'])}</b> – {e(det.get('duplicate_reason', ''))}</p>" \
        if r["Duplicate_Of"] != "None" else ""
    return f"""
<details id="lead-{e(lead_id)}"><summary>{e(lead_id)} – {e(r['Client_Name'])} ({e(r['Market'])})
 · <span class="pill {e(r['Lead_Temperature'])}">{e(r['Lead_Temperature'])}</span>
 · probability {e(r['Buying_Probability_0_100'])} · confidence {e(r['Confidence_0_100'])}</summary>
<div class="grid2"><div><p><b>Previous remarks:</b> {e(src['Previous_Remarks'] or '—')}</p>
<p><b>Latest remarks:</b> {e(src['Latest_Remarks'] or '—')}</p>
<p class="muted">Recorded project: {e(src['Recorded_Project'] or '—')} · Recorded budget: {e(src['Recorded_Budget'] or '—')}
 · Status: {e(src['Current_Status'])} · Last contact: {e(src['Last_Contact_Date'])}</p></div>
<div><b>Score breakdown</b><ul>{breakdown}</ul></div></div>
<div class="tbl"><table><tr><th>Field</th><th>Value</th><th>Evidence (exact quote)</th><th>Confidence</th>
<th>Source</th><th>Conflict</th></tr>{''.join(rows)}</table></div>
<p><b>Next action:</b> {e(r['Next_Action'])}</p><p><b>Suggested WhatsApp:</b> {e(r['Suggested_WhatsApp'])}</p>{dup}
</details>"""


def render_page(leads, out, details, temps, markets, min_prob, source_label, downloads_html) -> str:
    view = out[out["Lead_Temperature"].isin(temps) & out["Market"].isin(markets)
               & (out["Buying_Probability_0_100"] >= min_prob)]
    counts = out["Lead_Temperature"].value_counts()
    all_markets = sorted(out["Market"].unique())

    temp_opts = "".join(f'<option value="{t}" {"selected" if t in temps else ""}>{t}</option>'
                        for t in ["Hot", "Warm", "Cold"])
    market_opts = "".join(f'<option value="{e(m)}" {"selected" if m in markets else ""}>{e(m)}</option>'
                          for m in all_markets)

    priority = priority_list(view)  # same ranking as the Priority_List sheet in the Excel file
    prio_cols = ["Rank", "Lead_ID", "Client_Name", "Market", "Lead_Temperature", "Buying_Probability_0_100",
                 "Confidence_0_100", "Budget", "Site_Visit_Status", "Next_Action"]
    src_by_id = leads.set_index("Lead_ID")
    full_rows_in_priority_order = view.set_index("Lead_ID").loc[priority["Lead_ID"]]
    detail_html = "".join(_lead_detail(i, r, src_by_id.loc[i], details[i])
                          for i, r in full_rows_in_priority_order.iterrows())

    dups = out[out["Duplicate_Of"] != "None"].copy()
    dups["Reason"] = dups["Lead_ID"].map(lambda i: details[i].get("duplicate_reason", ""))
    conflicts = out[out["Conflict_Flags"] != "None"]
    weights = "".join(f"<li>{e(k)}: {v:+d}</li>" for k, v in WEIGHTS.items())

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>WCR Lead Intelligence</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>🏠 White Collar Realty – Lead Intelligence</h1>
<div class="muted">Rule-based, evidence-backed extraction. Anything not in the remarks = <b>Not Available</b>.
 Source: {e(source_label)}</div>

<div class="cards"><div class="card">Total leads<b>{len(out)}</b></div>
<div class="card">🔥 Hot<b>{counts.get('Hot', 0)}</b></div><div class="card">🌤 Warm<b>{counts.get('Warm', 0)}</b></div>
<div class="card">❄ Cold<b>{counts.get('Cold', 0)}</b></div></div>

<form class="bar" method="get" action="/">
<label>Temperature<select name="temp" multiple size="3">{temp_opts}</select></label>
<label>Market<select name="market" multiple size="3">{market_opts}</select></label>
<label>Min probability<input type="number" name="min_prob" min="0" max="100" step="5" value="{min_prob}"></label>
<button type="submit">Apply filters</button> <a class="btn" href="/">Reset</a></form>

<form class="bar" method="post" action="/upload" enctype="multipart/form-data">
<label>Process your own file (.xlsx with a Lead_Data sheet)<input type="file" name="file" accept=".xlsx" required></label>
<button type="submit">Process Leads</button> {downloads_html}</form>

<nav><a href="#priority">Priority View</a><a href="#full">Full Output</a><a href="#detail">Lead Detail</a>
<a href="#dupes">Duplicates &amp; Conflicts</a><a href="#how">How scoring works</a>
<a href="/before-after">Before vs After</a></nav>

<h2 id="priority">Priority View ({len(priority)} leads)</h2>{_row_table(priority, prio_cols)}
<h2 id="full">Full Output</h2>{_row_table(view, list(out.columns), wide=True)}
<h2 id="detail">Lead Detail</h2><p class="muted">Click a lead to see each field with its evidence, confidence and score breakdown.</p>{detail_html}
<h2 id="dupes">Duplicates</h2>{_row_table(dups, ["Lead_ID", "Client_Name", "Duplicate_Of", "Reason"]) if len(dups) else "<p>No duplicates found.</p>"}
<h2>Conflicts (latest remark wins, change is recorded)</h2>{_row_table(conflicts, ["Lead_ID", "Client_Name", "Conflict_Flags"]) if len(conflicts) else "<p>No conflicts found.</p>"}
<h2 id="how">How Buying Probability is calculated</h2>
<p>Start at 40, add/subtract each rule that fires, clamp to 0–100. Hot ≥ 70 · Warm 40–69 · Cold &lt; 40.
 Not interested / do-not-follow-up / no investment for 12+ months is always Cold.</p><ul>{weights}</ul>
<p><b>Timeline points</b> (latest remark wins; a previous timeline never adds points once the latest
 remark changes it): CONFIRMED ≤1 month +20 · ≤3 months +10 · ≤6 months +5 · TENTATIVE ≤1 month +10 ·
 ≤3 months +5 · ≤6 months +2 · VAGUE / UNCERTAIN / NONE 0 · LONG_DELAY (&gt;6 months) {WEIGHTS['far_timeline']}.</p>
</div></body></html>"""


# ---------------------------------------------------------------- routes
@app.get("/", response_class=HTMLResponse)
def index(temp: list[str] = Query(default=[]), market: list[str] = Query(default=[]), min_prob: int = 0):
    leads, out, details = default_results()
    temps = temp or ["Hot", "Warm", "Cold"]
    markets = market or sorted(out["Market"].unique())
    downloads = ('<a class="btn" href="/download.csv">⬇ CSV</a> <a class="btn" href="/download.xlsx">⬇ XLSX '
                 '(All_Leads + Priority_List)</a> <a class="btn" href="/priority_list.csv">⬇ Priority list CSV</a>')
    return render_page(leads, out, details, temps, markets, min_prob,
                       "data/WCR_AI_Intern_Practical_Candidate_Pack.xlsx (Lead_Data)", downloads)


@app.post("/upload", response_class=HTMLResponse)
async def upload(file: UploadFile = File(...)):
    try:
        leads = load_leads(io.BytesIO(await file.read()))
        out, details = process_all(leads)
    except Exception as exc:
        return HTMLResponse(f"<p>Could not process the file: {e(exc)}</p><p><a href='/'>Back</a></p>", status_code=400)
    # Stateless: embed the downloads directly in the page (nothing is stored on the server)
    csv_b64 = base64.b64encode(out.to_csv(index=False).encode("utf-8-sig")).decode()
    xlsx_b64 = base64.b64encode(to_excel_bytes(out)).decode()
    downloads = (f'<a class="btn" download="processed_leads.csv" href="data:text/csv;base64,{csv_b64}">⬇ CSV</a> '
                 f'<a class="btn" download="processed_leads.xlsx" href="data:application/vnd.openxmlformats-'
                 f'officedocument.spreadsheetml.sheet;base64,{xlsx_b64}">⬇ XLSX</a> <a class="btn" href="/">Back to sample data</a>')
    return render_page(leads, out, details, ["Hot", "Warm", "Cold"], sorted(out["Market"].unique()), 0,
                       f"uploaded file {file.filename}", downloads)


@app.get("/download.csv")
def download_csv():
    _, out, _ = default_results()
    return Response(out.to_csv(index=False).encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=processed_leads.csv"})


@app.get("/download.xlsx")
def download_xlsx():
    _, out, _ = default_results()
    return Response(to_excel_bytes(out),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=processed_leads.xlsx"})


@app.get("/priority_list.csv")
def download_priority_csv():
    _, out, _ = default_results()
    return Response(priority_list(out).to_csv(index=False).encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=priority_list.csv"})


_COMPARE_CACHE: dict = {}
SCENARIO_FILE = Path(__file__).resolve().parent.parent / "data" / "test_scenarios.xlsx"


def comparison_table() -> pd.DataFrame:
    """Legacy engine vs current engine, computed live on the bundled files (cached per instance)."""
    if "table" not in _COMPARE_CACHE:
        from src.compare import build_comparison
        parts = [build_comparison(load_leads(), "Original leads (L001–L024)")]
        if SCENARIO_FILE.exists():
            parts.append(build_comparison(load_leads(SCENARIO_FILE), "Test scenarios (L900–L902)"))
        _COMPARE_CACHE["table"] = pd.concat(parts, ignore_index=True)
    return _COMPARE_CACHE["table"]


@app.get("/before-after", response_class=HTMLResponse)
def before_after():
    table = comparison_table()
    cols = [c for c in table.columns if c != "Dataset"]
    sections = []
    for dataset, part in table.groupby("Dataset", sort=False):
        head = "".join(f"<th>{e(c)}</th>" for c in cols)
        body = "".join(
            f'<tr class="{e(r["Temp_After"])}">' + "".join(f"<td>{e(r[c])}</td>" for c in cols) + "</tr>"
            for _, r in part.iterrows())
        changed = int((part["Score_Change"] != 0).sum())
        sections.append(f"<h2>{e(dataset)}</h2><p class='muted'>Score changed for {changed} of {len(part)} leads."
                        f"</p><div class='tbl'><table class='wide'><tr>{head}</tr>{body}</table></div>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Before vs After</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>Before vs After – round 1 engine vs round 2 engine</h1>
<p class="muted">Both engines run live on the same leads every time this page is built: the frozen
round-1 engine (<code>legacy/engine_v1</code>) and the current engine (<code>src/</code>).
"Changed because" is derived from the two score breakdowns – nothing is pasted.</p>
<nav><a href="/">← Back to dashboard</a><a href="/before-after.csv">⬇ before_after.csv</a></nav>
{''.join(sections)}</div></body></html>"""


@app.get("/before-after.csv")
def before_after_csv():
    return Response(comparison_table().to_csv(index=False).encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=before_after.csv"})


@app.get("/api/leads")
def api_leads():
    _, out, _ = default_results()
    return JSONResponse(out.to_dict(orient="records"))
