"""fees_checker.py

Fees Checker — compares the itemized charges against the grouped charges.

For every patient folder under `output/`:
  1. Parse the folder name to get HPERCODE, ADM date and DIS date.
  2. Look up the encounter (ENCCode) in `hadmlog` (read-only).
  3. Sum itemized charges (detail, excluding 'PROC'):
       SELECT SUM(pcchrgamt) FROM hpatchrgdtl
       WHERE enccode=%s AND chargcode!='PROC'
  4. Sum grouped charges:
       SELECT SUM(grpamount) FROM hpatgrpchrg
       WHERE enccode=%s AND hpercode=%s
  5. Compare the two totals; hpatcon1 is used ONLY for the ADM/DIS
     date check (ptotalactualchargeshci is not compared).
  6. Convert hpatcon1 admission/discharge datetimes to YYYY/MM/DD and
     compare them against the folder-name ADM/DIS dates.
  7. Flag any mismatch: itemized total != grouped charges, or dates
     that do not match, or missing records.

Outputs (in the project root):
  - fees_checker_report.csv   plain CSV data
  - fees_checker_report.xlsx  same data with RED background rows for mismatches
  - The signed-date columns (hprofserv.pdoctorsigndate,
    hpatcon1.consentdate, hpatcon1.authsigndate) show SOLID RED cells when
    the date is blank so missing dates are visible at a glance
  - "Ready to Generate XML" verdict column: solid GREEN "YES" when the
    patient is ready (MATCH + all three signed dates + ADM/DIS dates
    match), solid RED "NO" otherwise, with a consolidated
    "XML READY CHECK" remark listing what blocks generation

Read-only: never writes to the hospital database.

Standalone test:  python fees_checker.py
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import re
import sys
from pathlib import Path

from core.hbsys_connection import create_hbsys_connection
from core.activity_logger import logger

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
except Exception:  # pragma: no cover - optional dependency
    Workbook = None

BASE_DIR = Path(r"C:\claims_bot")
OUTPUT_DIR = BASE_DIR / "output"
REPORT_CSV = BASE_DIR / "fees_checker_report.csv"
REPORT_XLSX = BASE_DIR / "fees_checker_report.xlsx"

FOLDER_RE = re.compile(
    r"^(?P<name>.+?) - (?P<hper>\d{15}) - "
    r"ADM(?P<adm>\d{8})_DIS(?P<dis>\d{8})$"
)

TOLERANCE = 0.01  # decimal rounding allowance (PHP)

HEADERS = [
    "Patient Folder",
    "Hospital No (HPERCODE)",
    "Encounter (ENCCODE)",
    "Folder ADM",
    "Folder DIS",
    "hpatcon1 ADM",
    "hpatcon1 DIS",
    "ADM Match",
    "DIS Match",
    "Itemized Total (SUM pcchrgamt hpatchrgdtl)",
    "Group Charges (SUM grpamount hpatgrpchrg)",
    "Member PHIC No (hpatcon.memphicnum)",
    "Employed (hphiclog.typemem)",
    "Employee No (hphiclog.phicnum2)",
    "Employee Name (hphiclog.empname)",
    "Prof Fee Sign Date (hprofserv.pdoctorsigndate)",
    "Consent Date (hpatcon1.consentdate)",
    "Auth Sign Date (hpatcon1.authsigndate)",
    "Difference",
    "Status",
    "Ready to Generate XML",
    "Remarks",
]


# ---------------------------------------------------------------- helpers


def _fmt_date(d: dt.date | dt.datetime | None) -> str:
    """YYYY/MM/DD string (the format used by the folder ADM/DIS labels)."""
    if d is None:
        return ""
    if isinstance(d, dt.datetime):
        d = d.date()
    return d.strftime("%Y/%m/%d")


def _money(value) -> str:
    if value is None:
        return ""
    return f"{float(value):,.2f}"


def parse_patient_folder(folder_name: str) -> dict | None:
    """Extract hpercode + ADM/DIS dates from a patient folder name."""
    m = FOLDER_RE.match(folder_name.strip())
    if not m:
        return None
    adm = dt.datetime.strptime(m.group("adm"), "%Y%m%d").date()
    dis = dt.datetime.strptime(m.group("dis"), "%Y%m%d").date()
    return {
        "name": folder_name.strip(),
        "hper": m.group("hper"),
        "adm": adm,
        "dis": dis,
        "adm_str": _fmt_date(adm),
        "dis_str": _fmt_date(dis),
    }


def _query_all(cur, sql: str, params=()):
    cur.execute(sql, params)
    return list(cur.fetchall())


def _query_one(cur, sql: str, params=()):
    cur.execute(sql, params)
    return cur.fetchone()


# ---------------------------------------------------------------- core check


def check_patient_folder(folder_name: str, cur) -> dict:
    """Run the fees comparison for one patient folder (read-only)."""
    row = {
        "Patient Folder": folder_name,
        "Hospital No (HPERCODE)": "",
        "Encounter (ENCCODE)": "",
        "Folder ADM": "",
        "Folder DIS": "",
        "hpatcon1 ADM": "",
        "hpatcon1 DIS": "",
        "ADM Match": "",
        "DIS Match": "",
        "Itemized Total (SUM pcchrgamt hpatchrgdtl)": "",
        "Group Charges (SUM grpamount hpatgrpchrg)": "",
        "Member PHIC No (hpatcon.memphicnum)": "",
        "Employed (hphiclog.typemem)": "",
        "Employee No (hphiclog.phicnum2)": "",
        "Employee Name (hphiclog.empname)": "",
        "Prof Fee Sign Date (hprofserv.pdoctorsigndate)": "",
        "Consent Date (hpatcon1.consentdate)": "",
        "Auth Sign Date (hpatcon1.authsigndate)": "",
        "Difference": "",
        "Status": "CHECK",
        "Remarks": "",
    }

    parsed = parse_patient_folder(folder_name)
    if not parsed:
        row["Status"] = "SKIPPED"
        row["Remarks"] = "Folder name format not recognized (no hpercode/ADM-DIS)"
        return row

    row["Hospital No (HPERCODE)"] = parsed["hper"]
    row["Folder ADM"] = parsed["adm_str"]
    row["Folder DIS"] = parsed["dis_str"]

    # --- locate encounter via hadmlog (read-only) ---------------------
    admissions = _query_all(
        cur,
        "SELECT enccode, admdate, disdate FROM hadmlog "
        "WHERE hpercode=%s ORDER BY admdate",
        (parsed["hper"],),
    )
    if not admissions:
        row["Status"] = "NO RECORD"
        row["Remarks"] = "NO hadmlog record for this hospital number"
        return row

    chosen = None
    for enc_row in admissions:
        adm_d = enc_row["admdate"]
        if isinstance(adm_d, dt.datetime):
            adm_d = adm_d.date()
        if adm_d == parsed["adm"]:
            chosen = enc_row
            break
    if chosen is None:
        chosen = admissions[0]
        row["Remarks"] += (
            f"Admission date {row['Folder ADM']} not found in hadmlog; "
            f"using earliest encounter. "
        )

    enccode = str(chosen["enccode"]).strip()
    row["Encounter (ENCCODE)"] = enccode

    # --- itemized charges: hpatchrgdtl (read-only) -------------------
    # NOTE: hpatchrgdtl has NO hpercode column; enccode is the key.
    dtl_row = _query_one(
        cur,
        "SELECT SUM(pcchrgamt) AS total FROM hpatchrgdtl "
        "WHERE enccode=%s AND chargcode!='PROC'",
        (enccode,),
    )
    itemized = float(dtl_row["total"] or 0) if dtl_row else 0.0
    row["Itemized Total (SUM pcchrgamt hpatchrgdtl)"] = _money(itemized)

    # --- grouped charges: hpatgrpchrg (read-only) --------------------
    grp_row = _query_one(
        cur,
        "SELECT SUM(grpamount) AS total FROM hpatgrpchrg "
        "WHERE enccode=%s AND hpercode=%s",
        (enccode, parsed["hper"]),
    )
    grouped = float(grp_row["total"] or 0) if grp_row else 0.0
    row["Group Charges (SUM grpamount hpatgrpchrg)"] = _money(grouped)

    # --- member PHIC number: hpatcon.memphicnum (read-only) ----------
    # hpatcon (not hpatcon1) holds the member PHIC number, keyed by enccode.
    mem = _query_one(
        cur,
        "SELECT memphicnum FROM hpatcon WHERE enccode=%s",
        (enccode,),
    )
    memphic = str(mem["memphicnum"] or "").strip() if mem else ""
    row["Member PHIC No (hpatcon.memphicnum)"] = memphic

    # --- employee check: hphiclog (read-only) -------------------------
    # hphiclog has NO enccode; it is keyed by hpercode. typemem '01' =
    # employed private, '02' = employed government. For those members the
    # phicnum2 must be 12 digits and empname must not be blank.
    EMPLOYED_TYPES = ("01", "02")
    phic = _query_one(
        cur,
        "SELECT typemem, phicnum2, empname FROM hphiclog "
        "WHERE hpercode=%s AND typemem IN (%s, %s) LIMIT 1",
        (parsed["hper"], EMPLOYED_TYPES[0], EMPLOYED_TYPES[1]),
    )
    if phic:
        typemem = str(phic["typemem"] or "").strip()
        phicnum2 = str(phic["phicnum2"] or "").strip()
        empname = str(phic["empname"] or "").strip()
        row["Employed (hphiclog.typemem)"] = typemem
        row["Employee No (hphiclog.phicnum2)"] = phicnum2
        row["Employee Name (hphiclog.empname)"] = empname
        label = "employed private (01)" if typemem == "01" else "employed government (02)"
        if not phicnum2.isdigit() or len(phicnum2) != 12:
            row["Remarks"] += (
                f"EMPLOYEE CHECK: member is {label} but phicnum2 is "
                f"'{phicnum2 or 'BLANK'}' — dapat 12 digits. "
            )
        if not empname:
            row["Remarks"] += (
                f"EMPLOYEE CHECK: member is {label} but empname is BLANK. "
            )

    # --- doctor signed date: hprofserv (read-only) -------------------
    # hprofserv may hold several professional-fee rows for one encounter;
    # some rows are intentionally left blank by the hospital, so only the
    # non-empty dates are collected. When EVERY row is blank the column
    # stays empty and is shown SOLID RED in the XLSX report.
    prof_rows = _query_all(
        cur,
        "SELECT pdoctorsigndate FROM hprofserv WHERE enccode=%s",
        (enccode,),
    )
    prof_dates = []
    for r in prof_rows:
        formatted = _fmt_date(r["pdoctorsigndate"])
        if formatted and formatted not in prof_dates:
            prof_dates.append(formatted)
    prof_dates.sort()
    row["Prof Fee Sign Date (hprofserv.pdoctorsigndate)"] = " | ".join(prof_dates)

    # --- summary of fees: hpatcon1 used ONLY for ADM/DIS dates --------
    # (ptotalactualchargeshci is intentionally NOT part of the comparison)
    con = _query_one(
        cur,
        "SELECT padmissiondatetime, pdischargedatetime, "
        "consentdate, authsigndate "
        "FROM hpatcon1 WHERE enccode=%s",
        (enccode,),
    )
    if not con:
        row["Status"] = "NO RECORD"
        row["Remarks"] += "NO hpatcon1 record for this encounter"
        return row

    row["hpatcon1 ADM"] = _fmt_date(con["padmissiondatetime"])
    row["hpatcon1 DIS"] = _fmt_date(con["pdischargedatetime"])

    consent_date = _fmt_date(con["consentdate"])
    auth_date = _fmt_date(con["authsigndate"])
    row["Consent Date (hpatcon1.consentdate)"] = consent_date
    row["Auth Sign Date (hpatcon1.authsigndate)"] = auth_date

    # --- date match --------------------------------------------------
    adm_match = row["hpatcon1 ADM"] == row["Folder ADM"]
    dis_match = row["hpatcon1 DIS"] == row["Folder DIS"]
    row["ADM Match"] = "YES" if adm_match else "NO"
    row["DIS Match"] = "YES" if dis_match else "NO"
    if not adm_match:
        row["Remarks"] += f"ADM date mismatch: folder {row['Folder ADM']} vs hpatcon1 {row['hpatcon1 ADM'] or '(no admission datetime)'}. "
    if not dis_match:
        row["Remarks"] += f"DIS date mismatch: folder {row['Folder DIS']} vs hpatcon1 {row['hpatcon1 DIS'] or '(no discharge datetime)'}. "

    # --- amount match: hpatchrgdtl vs hpatgrpchrg --------------------
    diff = round(itemized - grouped, 2)
    row["Difference"] = _money(abs(diff))
    if itemized <= TOLERANCE:
        # zero/null itemized total -> bill was not finalized in HBSYS
        row["Status"] = "NO FINAL BILL"
        mem_note = f"hpatcon.memphicnum: {memphic}" if memphic else "hpatcon.memphicnum: WALA"
        row["Remarks"] += (
            f"Itemized total (hpatchrgdtl) is PHP 0.00/null — "
            f"hindi pa na-FINAL BILL sa HBSYS. "
            f"{mem_note}. "
        )
        if abs(diff) > TOLERANCE:
            row["Remarks"] += (
                f"Grouped (hpatgrpchrg) shows PHP {_money(grouped)}."
            )
    elif abs(diff) <= TOLERANCE:
        row["Status"] = "MATCH"
        row["Remarks"] += (
            f"Itemized (hpatchrgdtl) and grouped (hpatgrpchrg) "
            f"both equal PHP {_money(itemized)}."
        )
    else:
        row["Status"] = "MISMATCH"
        row["Remarks"] += (
            f"TOTAL MISMATCH: itemized PHP {_money(itemized)} "
            f"(hpatchrgdtl) vs grouped PHP {_money(grouped)} "
            f"(hpatgrpchrg) (difference PHP {_money(abs(diff))})."
        )
    return row


# ---------------------------------------------------------------- reports


def ready_to_generate_xml(row: dict) -> tuple[str, str]:
    """Decide XML readiness for one checked folder: (verdict, problems).

    A patient is ready to generate the XML when ALL of these hold:
      - fees Status is MATCH (itemized == grouped charges),
      - the folder ADM/DIS dates agree with hpatcon1,
      - the three signed-date fields (professional fee, consent,
        authorization) all have a value.
    Returns ("YES", "") when ready, otherwise ("NO", problem list).
    """
    problems = []
    if row.get("Status") != "MATCH":
        problems.append(f"status is {row.get('Status') or 'unknown'}")
    if row.get("ADM Match") != "YES":
        problems.append("ADM date mismatch")
    if row.get("DIS Match") != "YES":
        problems.append("DIS date mismatch")
    if not row.get("Prof Fee Sign Date (hprofserv.pdoctorsigndate)"):
        problems.append("hprofserv.pdoctorsigndate BLANK")
    if not row.get("Consent Date (hpatcon1.consentdate)"):
        problems.append("hpatcon1.consentdate BLANK")
    if not row.get("Auth Sign Date (hpatcon1.authsigndate)"):
        problems.append("hpatcon1.authsigndate BLANK")
    if problems:
        return "NO", "; ".join(problems)
    return "YES", ""


def iter_patient_folders():
    if not OUTPUT_DIR.is_dir():
        return
    for child in sorted(OUTPUT_DIR.iterdir()):
        if child.is_dir():
            yield child.name


def write_reports(rows: list[dict]) -> tuple[Path, Path]:
    """Write the CSV report and the XLSX report with red mismatch rows."""
    # Decide XML readiness for every row (single source of truth) and
    # append a consolidated "XML READY CHECK" remark when not ready.
    for row in rows:
        verdict, problems = ready_to_generate_xml(row)
        row["Ready to Generate XML"] = verdict
        if problems:
            row["Remarks"] = (
                (row.get("Remarks") or "") + f"XML READY CHECK: {problems}. "
            )

    csv_path = REPORT_CSV
    try:
        f = open(csv_path, "w", newline="", encoding="utf-8-sig")
    except PermissionError:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = BASE_DIR / f"fees_checker_report_{stamp}.csv"
        f = open(csv_path, "w", newline="", encoding="utf-8-sig")

    with f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    try:
        xlsx_path = write_xlsx(rows, REPORT_XLSX)
    except PermissionError:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        xlsx_path = write_xlsx(rows, BASE_DIR / f"fees_checker_report_{stamp}.xlsx")
    return csv_path, xlsx_path


def write_xlsx(rows: list[dict], path: Path) -> Path:
    """Write the Excel report with status colors and the XML-readiness column."""
    if Workbook is None:
        return path

    wb = Workbook()
    ws = wb.active
    ws.title = "Fees Checker"

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    red_fill = PatternFill("solid", fgColor="FFC7CE")      # light red
    red_status_fill = PatternFill("solid", fgColor="FF0000")  # solid red
    red_status_font = Font(color="FFFFFF", bold=True)
    green_fill = PatternFill("solid", fgColor="C6EFCE")    # light green
    amber_fill = PatternFill("solid", fgColor="FFE699")    # light amber (no final bill)
    amber_status_fill = PatternFill("solid", fgColor="FFC000")  # solid amber
    blank_date_fill = PatternFill("solid", fgColor="FF0000")  # solid red (missing date)
    blank_date_font = Font(color="FFFFFF", bold=True)
    ready_fill = PatternFill("solid", fgColor="008000")      # solid green (ready YES)
    not_ready_fill = PatternFill("solid", fgColor="FF0000")  # solid red (ready NO)
    ready_font = Font(color="FFFFFF", bold=True)
    thin = Side(style="thin", color="B0B0B0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.append(HEADERS)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    status_col = HEADERS.index("Status") + 1
    ready_col = HEADERS.index("Ready to Generate XML") + 1
    date_cols = [
        HEADERS.index("Prof Fee Sign Date (hprofserv.pdoctorsigndate)") + 1,
        HEADERS.index("Consent Date (hpatcon1.consentdate)") + 1,
        HEADERS.index("Auth Sign Date (hpatcon1.authsigndate)") + 1,
    ]
    for row in rows:
        ws.append([row.get(h, "") for h in HEADERS])
        excel_row = ws.max_row
        status = row.get("Status", "")
        ready = row.get("Ready to Generate XML") == "YES"
        bad = status not in ("MATCH", "SKIPPED")
        for cell in ws[excel_row]:
            cell.border = border
            if ready:
                # Ready to Generate XML -> whole row green.
                cell.fill = green_fill
            elif status == "MATCH":
                # Fees matched but something still blocks XML generation.
                cell.fill = red_fill
            elif status == "NO FINAL BILL":
                cell.fill = amber_fill
            elif bad:
                cell.fill = red_fill
            if status in ("MISMATCH", "NO RECORD"):
                if cell.column == status_col:
                    cell.fill = red_status_fill
                    cell.font = red_status_font
                    cell.alignment = Alignment(horizontal="center")
            elif status == "NO FINAL BILL":
                if cell.column == status_col:
                    cell.fill = amber_status_fill
                    cell.font = Font(color="7F3F00", bold=True)
                    cell.alignment = Alignment(horizontal="center")

        # The "Ready to Generate XML" verdict cell: solid green YES,
        # solid red NO (applies to every row, including SKIPPED/ERROR).
        ready_cell = ws.cell(row=excel_row, column=ready_col)
        ready_cell.alignment = Alignment(horizontal="center")
        if ready:
            ready_cell.fill = ready_fill
        else:
            ready_cell.fill = not_ready_fill
        ready_cell.font = ready_font

        # Blank signed-date cells get a SOLID RED fill so missing dates
        # are visible even inside green rows.
        # SKIPPED and ERROR rows are excluded — their empty cells are not
        # missing dates, just rows without usable data.
        if status not in ("SKIPPED", "ERROR"):
            for col in date_cols:
                cell = ws.cell(row=excel_row, column=col)
                if not str(cell.value or "").strip():
                    cell.fill = blank_date_fill
                    cell.font = blank_date_font
                    cell.alignment = Alignment(horizontal="center")

    # column widths (best effort)
    for i, header in enumerate(HEADERS, start=1):
        width = max(12, min(45, len(header) + 4))
        if header in ("Patient Folder", "Remarks", "Encounter (ENCCODE)"):
            width = 42
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    ws.freeze_panes = "A2"

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def run_check() -> tuple[list[dict], Path, Path]:
    """Run the full check against HBSys (read-only) and write reports."""
    logger.info("Fees Checker started")
    folders = list(iter_patient_folders())
    logger.info(f"Found {len(folders)} patient folders in {OUTPUT_DIR}")

    conn = create_hbsys_connection()
    rows: list[dict] = []
    try:
        with conn.cursor() as cur:
            for folder in folders:
                try:
                    row = check_patient_folder(folder, cur)
                except Exception as exc:  # noqa: BLE001 - per-folder isolation
                    row = {
                        "Patient Folder": folder,
                        "Hospital No (HPERCODE)": "",
                        "Encounter (ENCCODE)": "",
                        "Folder ADM": "",
                        "Folder DIS": "",
                        "hpatcon1 ADM": "",
                        "hpatcon1 DIS": "",
                        "ADM Match": "",
                        "DIS Match": "",
                        "Itemized Total (SUM pcchrgamt hpatchrgdtl)": "",
                        "Group Charges (SUM grpamount hpatgrpchrg)": "",
                        "Member PHIC No (hpatcon.memphicnum)": "",
                        "Employed (hphiclog.typemem)": "",
                        "Employee No (hphiclog.phicnum2)": "",
                        "Employee Name (hphiclog.empname)": "",
                        "Prof Fee Sign Date (hprofserv.pdoctorsigndate)": "",
                        "Consent Date (hpatcon1.consentdate)": "",
                        "Auth Sign Date (hpatcon1.authsigndate)": "",
                        "Difference": "",
                        "Status": "ERROR",
                        "Remarks": f"DB error: {exc}",
                    }
                    logger.error(f"Fees check failed for {folder}: {exc}")
                rows.append(row)
    finally:
        conn.close()

    csv_path, xlsx_path = write_reports(rows)

    summary = {
        "total": len(rows),
        "match": sum(1 for r in rows if r["Status"] == "MATCH"),
        "mismatch": sum(1 for r in rows if r["Status"] == "MISMATCH"),
        "no_record": sum(1 for r in rows if r["Status"] == "NO RECORD"),
        "error": sum(1 for r in rows if r["Status"] == "ERROR"),
        "skipped": sum(1 for r in rows if r["Status"] == "SKIPPED"),
        "ready_xml": sum(1 for r in rows if r.get("Ready to Generate XML") == "YES"),
    }
    logger.success(
        f"Fees Checker done: {summary['ready_xml']} READY for XML, "
        f"{summary['match']} MATCH, "
        f"{summary['mismatch']} MISMATCH, {summary['no_record']} NO RECORD, "
        f"{summary['error']} ERROR, {summary['skipped']} SKIPPED "
        f"(total {summary['total']})"
    )
    return rows, csv_path, xlsx_path


# ---------------------------------------------------------------- main


def open_report(path: Path) -> None:
    """Open the report with the default application (Windows)."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(path)])
        logger.info(f"Opened report: {path}")
    except Exception as exc:  # noqa: BLE001 - never crash the checker
        logger.warning(f"Could not open report {path}: {exc}")


def main() -> int:
    print("=" * 60)
    print("  FEES CHECKER - Itemized vs Grouped Charges")
    print("=" * 60)
    rows, csv_path, xlsx_path = run_check()

    print()
    print(f"CSV report : {csv_path}")
    print(f"XLSX report: {xlsx_path}")
    print()
    for r in rows:
        flag = "  " if r["Status"] == "MATCH" else "!!"
        print(f"[{r['Status']:<9}] {flag} {r['Patient Folder']}")
        if r["Status"] != "MATCH":
            print(f"          {r['Remarks']}")
    print()
    print("Mismatch rows are highlighted in RED in the XLSX report.")
    open_report(xlsx_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
