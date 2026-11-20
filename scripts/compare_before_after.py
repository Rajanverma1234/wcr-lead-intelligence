"""Run the legacy engine (round 1) and the current engine on the same leads and compare.

  (a) all 24 original leads   - data/WCR_AI_Intern_Practical_Candidate_Pack.xlsx (read only)
  (b) the scenario leads      - data/test_scenarios.xlsx

Writes output/before_after.csv and output/before_after.md (same folder as processed_leads.xlsx).
Run:  python scripts/compare_before_after.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from src.compare import build_comparison, to_markdown  # noqa: E402
from src.loader import DEFAULT_INPUT, OUTPUT_DIR, load_leads  # noqa: E402

SCENARIOS = ROOT / "data" / "test_scenarios.xlsx"
OUT_DIR = OUTPUT_DIR  # output/


def main():
    parts = [build_comparison(load_leads(DEFAULT_INPUT), "Original leads (L001–L024)")]
    if SCENARIOS.exists():
        parts.append(build_comparison(load_leads(SCENARIOS), "Test scenarios (L900–L902)"))
    table = pd.concat(parts, ignore_index=True)
    OUT_DIR.mkdir(exist_ok=True)
    table.to_csv(OUT_DIR / "before_after.csv", index=False, encoding="utf-8-sig")
    (OUT_DIR / "before_after.md").write_text(to_markdown(table), encoding="utf-8")
    print(table[["Dataset", "Lead_ID", "Score_Before", "Score_After", "Temp_Before", "Temp_After"]]
          .to_string(index=False))
    print(f"\nWrote {OUT_DIR / 'before_after.csv'} and {OUT_DIR / 'before_after.md'}")


if __name__ == "__main__":
    main()
