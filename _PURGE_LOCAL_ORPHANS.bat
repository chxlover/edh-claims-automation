@echo off
REM ====================================================================
REM edh-claims-automation  --  LOCAL orphan-object purge (MAIN repo only)
REM ----------------------------------------------------------------------------
REM SCOPE: The PUBLIC GitHub history is ALREADY purged + force-pushed
REM   (main HEAD == cc3826f, 'git log --all -- screenshots' is empty,
REM    old commit b0f0f13 no longer exists).  Nothing here touches the
REM    network or rewrites history again.
REM
REM WHY THIS SCRIPT:
REM   19 unreachable objects (15 patient PNG blobs, including
REM   fa0abbb86622cdf8ad20a1060acc099394aa6c35, + 4 tree objects) still
REM   physically live inside C:\claims_bot\.git\objects\pack\*.pack. They
REM   are NOT reachable from any ref (so 'git log --all -- screenshots' is
REM   already empty) but are kept alive by the LINKED worktree below, whose
REM   own INDEX still stages the old screenshot trees.
REM
REM ACTIONS (ALL SAFE / LOCAL):
REM   * refresh ONLY the linked worktree's INDEX to its HEAD (NO -u, so the
REM     on-disk C:\...\d1cc2\screenshots files and logs\ are left intact)
REM   * expire the linked worktree's private reflog
REM   * expire the MAIN reflogs and run 'gc --prune=now' to delete the
REM     orphaned object bytes
REM
REM LEAVE INTACT: the backup mirror C:\claims_bot_git_backup_20260923.git
REM (it intentionally still contains the images). No pushes. No file
REM deletions outside .git object storage.
REM ====================================================================
setlocal
set "MAIN=C:\claims_bot"
set "WT=C:\Users\EDH-Admin\.cline\worktrees\d1cc2\claims_bot"

echo === STEP 1: worktrees ===
git -C "%MAIN%" worktree list
echo.

echo === STEP 2: refresh linked worktree INDEX only (no working-tree changes) ===
git -C "%WT%" reflog expire --expire=now --all
git -C "%WT%" read-tree --reset HEAD
if errorlevel 1 echo WARNING: worktree read-tree reported error 1>&2
echo.

echo === STEP 3: main reflogs + gc prune ===
git -C "%MAIN%" reflog expire --expire=now --all
git -C "%MAIN%" gc --prune=now --aggressive --quiet
if errorlevel 1 echo WARNING: gc reported error 1>&2
echo.

echo === STEP 3b: escalate ONLY if orphan blob still present ===
git -C "%MAIN%" cat-file -te fa0abbb86622cdf8ad20a1060acc099394aa6c35 2>nul
if not errorlevel 1 (
    echo orphan still present - repacking to drop unreachable from pack
    git -C "%MAIN%" repack -a -d
    git -C "%MAIN%" reflog expire --expire=now --all
    git -C "%MAIN%" gc --prune=now --aggressive --quiet
) else (
    echo orphan blob already purged
)
echo.

echo === STEP 4: VERIFY (expect 0 / FATAL) ===
echo -- 1) orphan blob must be GONE (expect 'fatal: ... Not a valid object name'):
git -C "%MAIN%" cat-file -te fa0abbb86622cdf8ad20a1060acc099394aa6c35 2>&1 | findstr /R "fatal"
echo -- 2) reachable hits on orphan SHA (must be 0):
git -C "%MAIN%" rev-list --objects --all | findstr /C:fa0abbb | find /C /V ""
echo -- 3) reachable objects with screenshots path (must be 0):
git -C "%MAIN%" rev-list --objects --all | findstr /R "screenshots" | find /C /V ""
echo -- 4) git log --all -- screenshots (must be empty):
git -C "%MAIN%" log --all --oneline -- screenshots
echo -- 5) main staged screenshots (must be 0):
git -C "%MAIN%" ls-files --cached --name-only -- screenshots | find /C /V ""
echo -- 6) linked worktree staged screenshots (must be 0):
git -C "%WT%" ls-files --cached --name-only -- screenshots | find /C /V ""
echo.

echo === STEP 5: object totals ===
git -C "%MAIN%" count-objects -v
echo.

echo === STEP 6: tidy stray temp files ===
for %%F in (_up_out.txt _up_err.txt _t1.txt) do if exist "%MAIN%\%%F" del /q "%MAIN%\%%F"
echo done
endlocal

