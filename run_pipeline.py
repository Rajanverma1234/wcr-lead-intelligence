"""CLI: process the Excel file and write output/processed_leads.{csv,xlsx} (no UI).

Usage:
    python run_pipeline.py                 # default data/ file
    python run_pipeline.py path/to/file.xlsx
"""
import sys
from pathlib import Path

import pandas as pd

from src.loader import DEFAULT_INPUT, OUTPUT_DIR, load_leads
from src.pipeline import process_all, save_outputs


def main():
    # Windows consoles may not print ₹ / → by default
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    leads = load_leads(source)
    out, _ = process_all(leads)
    csv_path, xlsx_path = save_outputs(out, OUTPUT_DIR)

    pd.set_option("display.width", 200)
    pd.set_option("display.max_colwidth", 40)
    print(out[["Lead_ID", "Budget", "Site_Visit_Status", "Lead_Temperature", "Buying_Probability_0_100"]]
          .to_string(index=False))
    counts = out["Lead_Temperature"].value_counts().to_dict()
    print(f"\n{len(out)} leads processed | Hot {counts.get('Hot', 0)} | Warm {counts.get('Warm', 0)} | Cold {counts.get('Cold', 0)}")
    print(f"Wrote {csv_path}\nWrote {xlsx_path}")


if __name__ == "__main__":
    main()
