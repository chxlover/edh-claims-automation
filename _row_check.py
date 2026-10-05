"""Temp: does the real parser now see the rows it used to drop?"""
import sys
from pathlib import Path

sys.path.insert(0, "C:/claims_bot/date_fill_hbsys")

import hbsys_read_admission_history as reader  # noqa: E402

for name in sys.argv[1:]:
    path = Path(name)
    print(f"\n########## {path.name}")
    items = reader.read_ocr_items(path)
    print("  repaired date tokens:")
    for item in sorted(items, key=lambda i: (i.y, i.x)):
        if reader.DATE_RE.search(item.text):
            print(f"    y={item.y:6.1f} x={item.x:6.1f} {item.text!r}")
    rows = reader.parse_rows_with_positions(items)
    print(f"  parsed rows: {len(rows)}")
    for parsed in rows:
        print(
            f"    y={parsed.y:6.1f} {parsed.row.admission_date} -> "
            f"{parsed.row.discharge_date}  {parsed.row.encounter_type}"
        )
    recommendation = None
    try:
        from hbsys_rules import recommend_discharge_date

        recommendation = recommend_discharge_date([p.row for p in rows])
    except Exception as exc:
        print(f"  recommend failed: {exc}")
    if recommendation is not None:
        print(f"  recommendation: {recommendation.status} {recommendation.reason}")
