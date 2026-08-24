# HBSys Date Fill Assistant - Transfer Guide

Ito ang guide para ilipat ang program sa ibang computer.

## 1. Files Na Kokopyahin

Copy buong folder na ito:

```text
C:\Users\EDH-Admin\Documents\date_fill_hbsys
```

Minimum files needed:

```text
hbsys_fill_dates.py
hbsys_ready_claims.py
hbsys_read_admission_history.py
hbsys_rules.py
requirements.txt
README.md
TRANSFER_GUIDE.md
```

Optional pero useful:

```text
hbsys_inspect.py
hbsys_find_zero_dates.py
logs\
```

Recommended destination sa new computer:

```text
C:\Users\EDH-Admin\Documents\date_fill_hbsys
```

Pwede rin ibang path, basta doon ka mag-`cd` bago mag-run.

## 2. Claim Source Folder

Default source folder:

```text
C:\claims_bot\output
```

Sa main EDH Claims GUI, pwede nang pumili sa Preferences:

- Output Folder

Kung standalone PowerShell run sa ibang PC, pwede mong ipasa ang source:

```powershell
python .\hbsys_fill_dates.py --ready-dir "D:\Claims\output"
```

Ang folder names sa loob dapat ganito ang format:

```text
PATIENT NAME - HOSPITALNO - ADMYYYYMMDD_DISYYYYMMDD
```

Example:

```text
GERON, JIMMY SICAM - 000000000018987 - ADM20260611_DIS20260615
```

Source of truth ng automation:

- Hospital number
- Admission date
- Discharge date
- Patient name

## 3. Install Python Requirements

Open PowerShell:

```powershell
cd C:\Users\EDH-Admin\Documents\date_fill_hbsys
python --version
python -m pip install -r requirements.txt
```

Kung walang Python, install muna Python 3.x, then rerun command above.

## 4. Check Output Claims

Before live run:

```powershell
python .\hbsys_ready_claims.py
```

Or with a custom source:

```powershell
python .\hbsys_ready_claims.py --ready-dir "D:\Claims\output"
```

Dapat makita ang list ng patients:

```text
READY claims: 2
1. PATIENT NAME | hospital_no=... | admission=... | discharge=...
```

Kung `No READY claims found`, walang ipa-process.

## 5. HBSys Requirements

Bago mag-live run:

- HBSys must be open.
- HBSys must be maximized.
- Screen resolution should be `1920x1080`.
- Start screen should be the Billing screen with the Hospital No. input visible.
- Do not move mouse/keyboard while automation is running.
- Press `Ctrl+C` in PowerShell to stop if something is wrong.

## 6. Dry Run

Preview only, no clicking/typing:

```powershell
python .\hbsys_fill_dates.py --limit 1
```

Preview all output folders:

```powershell
python .\hbsys_fill_dates.py
```

## 7. First Live Test

Run one patient only:

```powershell
python .\hbsys_fill_dates.py --live --limit 1 --confirm-each
```

This is recommended after transferring to a new computer.

## 8. Live Run All Output Claims

Once tested:

```powershell
python .\hbsys_fill_dates.py --live
```

With confirmation before every patient:

```powershell
python .\hbsys_fill_dates.py --live --confirm-each
```

Run one exact hospital number:

```powershell
python .\hbsys_fill_dates.py --live --hospital-no 000000000018987
```

## 9. What The Script Does

For each output folder:

1. Enter hospital number.
2. Open Admit History.
3. Select confinement matching READY admission/discharge.
4. Click PHIC.
5. In PhilHealth Beneficiaries, select matching confinement.
6. If multiple rows have same confinement, compare patient name too.
7. Open Claim Form 2.
8. Go to Professional Fees / Charges.
9. Click Edit.
10. Fill Date Signed with discharge date.
11. Save, then click OK on PHIC popup.
12. Go to Consent.
13. Click Edit.
14. Fill both date fields with discharge date.
15. Save, then click OK on PHIC popup.
16. Close Claim Form 2.
17. Close PhilHealth Beneficiaries.
18. Continue until output folders are done.

## 10. Logs

Every run creates a CSV log:

```text
logs\hbsys_fill_run_YYYYMMDD_HHMMSS.csv
```

Check this if automation stops:

```text
done
needs_review_admission_history
needs_review_phic_beneficiaries
error: ...
```

## 11. Common Fixes

If hospital number is not typed correctly:

- Make sure HBSys is maximized.
- Make sure the Hospital No. input is visible.
- Run with `--confirm-each` and watch first patient.

If wrong confinement is selected:

- Check output folder admission/discharge dates.
- Check if patient name in output folder matches HBSys spelling.
- Use `--hospital-no` to test one patient only.

If OCR is slow:

- First OCR run can be slower.
- Later runs are usually faster.
