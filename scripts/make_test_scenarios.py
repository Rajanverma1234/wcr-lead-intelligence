"""Create data/test_scenarios.xlsx - assessment scenario leads in a SEPARATE file.

The original workbook (data/WCR_AI_Intern_Practical_Candidate_Pack.xlsx) is never touched.
Same columns and sheet name (Lead_Data) as the original, so the normal loader reads it.
All names / phones are dummy values. L902 has no Market on purpose (nothing may be invented).

Run:  python scripts/make_test_scenarios.py
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "test_scenarios.xlsx"

ROWS = [
    {
        "Lead_ID": "L900", "Client_Name": "Aarav Test", "Phone": "+91 90000 00900", "Market": "Gurugram",
        "Last_Contact_Date": "2026-08-17", "Current_Status": "Follow-up", "Recorded_Project": "",
        "Recorded_Budget": "₹5 Cr",
        "Previous_Remarks": "Client wants to purchase a property within 2 months with a budget of ₹5 Cr.",
        "Latest_Remarks": "Client will proceed only after selling his existing property. His wife also needs "
                          "to approve the final decision. Purchase timeline is currently uncertain.",
    },
    {
        "Lead_ID": "L901", "Client_Name": "Diya Test", "Phone": "+91 90000 00901", "Market": "Gurugram",
        "Last_Contact_Date": "2026-08-17", "Current_Status": "Interested", "Recorded_Project": "",
        "Recorded_Budget": "₹2.5 Cr",
        "Previous_Remarks": "Client looking for 3BHK in Gurgaon around ₹2.5 Cr, wants to close within 1 month.",
        "Latest_Remarks": "Client has increased budget to ₹3 Cr. Probably will close in 2-3 months, subject to "
                          "home loan approval. Asked for brochure and floor plan.",
    },
    {
        "Lead_ID": "L902", "Client_Name": "Kabir Test", "Phone": "+91 90000 00902", "Market": "",
        "Last_Contact_Date": "2026-08-17", "Current_Status": "Nurture", "Recorded_Project": "",
        "Recorded_Budget": "",
        "Previous_Remarks": "Client interested, no timeline shared.",
        "Latest_Remarks": "Client says he will buy sometime next year, not sure about location yet.",
    },
]


def main():
    with pd.ExcelWriter(OUT, engine="openpyxl") as writer:
        pd.DataFrame(ROWS).to_excel(writer, sheet_name="Lead_Data", index=False)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
