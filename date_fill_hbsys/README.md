# HBSys Date Fill Assistant

Goal: tulungan i-fill ang mga `00-00-0000` date fields sa HBSys PHIC Claim Form 2 gamit ang tamang `DATE DISCHARGED` mula sa Admission History.

## Best workflow

1. Open HBSys and go to the patient/billing record.
2. Run the inspector first:

   ```powershell
   python .\hbsys_inspect.py
   ```

3. Check the generated files inside `logs/`.
4. Kapag nababasa ng script ang grid at fields, saka natin gagawin ang controlled fill automation.

To read the currently open Admission History popup:

```powershell
python .\hbsys_read_admission_history.py
```

Expected output includes the detected rows and the recommended date to fill.

To list claims from the output folder:

```powershell
python .\hbsys_ready_claims.py
```

The output folder name is treated as the source of truth:

```text
PATIENT NAME - HOSPITALNO - ADMYYYYMMDD_DISYYYYMMDD
```

To preview the full automation without clicking/type:

```powershell
python .\hbsys_fill_dates.py --limit 1
```

First live test should process only one patient and ask before continuing:

```powershell
python .\hbsys_fill_dates.py --live --limit 1 --confirm-each
```

Only run without `--limit` after the one-patient test is correct.

## Rule for multiple confinement

Kapag may multiple rows sa Admission History:

1. Prefer row with `TYPE OF ENCOUNTER = ADMIT`.
2. Prefer row whose admission date/discharge date matches the claim period or visible date context.
3. If only one ADMIT row exists, recommend that row.
4. If still ambiguous, stop and require user selection.

For the sample screenshot, the safest recommended row is:

```text
Admission: 06/05/2026 04:30 AM
Discharged: 06/09/2026 01:12 PM
Type: ADMIT
Date to fill: 06-09-2026
```

## Why inspect first?

HBSys appears to be a legacy Windows program. Some grids expose their text to Windows automation, while others only paint pixels on screen. The inspector tells us which path is reliable:

- Direct UI automation: safer and more accurate.
- Coordinate automation: possible, but needs fixed window size and more testing.
- OCR fallback: useful only if the grid text is not exposed.
