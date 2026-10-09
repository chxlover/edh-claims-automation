"""Remaining-patient counter for the Date Fill / Final Bill workflow nodes.

Pure, line-driven and Tk-free: the caller forwards every workflow log line
(`gui/workflow_tab.WorkflowFrame._log` already receives all of them) and reads
`label` back for the status overlay. No parsing result is ever used to decide
WHICH patient to touch — it is display-only, so an unrecognised or malformed
line can only cost a count, never a safety check.

Contract (every rule below is anchored on text the nodes already print):

* ``[RUNNING] Date Fill (REGULAR, discharge date)``  -> start counting Date Fill
  ``Claims: 3``                                        -> the batch size
  ``[LIVE] processing <NAME> | <hosp no> | ...``      -> one patient finished
* ``[RUNNING] Final Bill (HBSys Billing -> Final)``   -> start counting Final Bill
  ``3 patient folder(s) under ..., 2 pending (NO FINAL BILL)`` -> the queue size
  ``[LIVE] (1/2) <FOLDER> | hospital no ...``          -> one patient finished
* ``[DONE]`` / ``[FAILED]`` / ``[STOPPED]`` / a new ``[RUNNING]``
  -> reset, so the next node never inherits the previous node's count.

A node whose size is not known yet reports no count (the caller keeps its
generic "Workflow running" text) rather than guessing a total.

Standalone test:

    python -m unittest tests.test_workflow_progress
    python core/workflow_progress.py
"""

from __future__ import annotations

import re

# -- node identity ----------------------------------------------------------

DATE_FILL = "date_fill"
FINAL_BILL = "final_bill"

# Short labels for the overlay; both must stay under
# gui.run_status_overlay.MAX_STATUS_CHARS together with the dots.
SHORT_LABEL = {DATE_FILL: "Date Fill", FINAL_BILL: "Final Bill"}

_RUNNING_RE = re.compile(r"^\[RUNNING\]\s+(?P<label>.*)$")
_NODE_END_RE = re.compile(r"^\[(DONE|FAILED|STOPPED|SKIPPED|REFUSED)\]")
_CLAIMS_RE = re.compile(r"^Claims:\s*(?P<count>\d+)\s*$")
_PROCESSING_RE = re.compile(r"^\[LIVE\]\s+processing\s+\S")
_FOLDERS_RE = re.compile(
    r"^(?:\[LIVE\]\s+)?(?P<total>\d+)\s+patient folder\(s\) under .*?"
    r"(?P<pending>\d+)\s+pending",
    re.IGNORECASE,
)
_ITEM_RE = re.compile(r"^\[LIVE\]\s+\((?P<done>\d+)/(?P<total>\d+)\)")


def node_kind(label: str) -> str | None:
    """Which counting mode a `[RUNNING] <label>` line means ('' when neither).

    Matching is on the registry label text, never on a guessed node id, and an
    unrecognised label simply yields None so the caller keeps its generic text.
    """
    text = str(label or "").strip().lower()
    if text.startswith("date fill"):
        return DATE_FILL
    if text.startswith("final bill"):
        return FINAL_BILL
    return None


class WorkflowProgress:
    """Remaining-patient state fed from the workflow log, one line at a time."""

    def __init__(self) -> None:
        self._kind: str = ""
        self._total: int = 0
        self._done: int = 0

    def reset(self) -> None:
        """Forget the current node (idle, stopped, or finished)."""
        self._kind = ""
        self._total = 0
        self._done = 0

    def feed(self, line: str) -> None:
        """Consume one workflow log line. Never raises."""
        try:
            self._feed(str(line or ""))
        except Exception:  # noqa: BLE001 - display-only counter.
            self.reset()

    def _feed(self, line: str) -> None:
        text = line.strip()
        if not text:
            return

        # A finished or restarted node clears the previous node's numbers, so
        # the overlay can never keep showing a stale count.
        if _NODE_END_RE.match(text):
            self.reset()
            return

        running = _RUNNING_RE.match(text)
        if running:
            kind = node_kind(running.group("label"))
            if kind is None:
                self.reset()
            else:
                self._kind = kind
                self._total = 0
                self._done = 0
            return

        # Lines are only counted while a countable node is active.
        if self._kind == DATE_FILL:
            claims = _CLAIMS_RE.match(text)
            if claims:
                self._total = int(claims.group("count"))
                self._done = 0
                return
            if _PROCESSING_RE.match(text):
                self._done += 1
                return
        elif self._kind == FINAL_BILL:
            folders = _FOLDERS_RE.match(text)
            if folders:
                # Only NO FINAL BILL folders are ever processed, so the
                # pending count is the real queue size.
                self._total = int(folders.group("pending"))
                self._done = 0
                return
            item = _ITEM_RE.match(text)
            if item:
                self._total = int(item.group("total"))
                self._done = int(item.group("done"))

    @property
    def kind(self) -> str:
        """Which node is being counted (DATE_FILL / FINAL_BILL / '')."""
        return self._kind

    @property
    def remaining(self) -> int:
        """Patients still to process; 0 when the total is not known yet."""
        return max(0, self._total - self._done)

    @property
    def label(self) -> str:
        """Short overlay text, or '' when there is nothing to count."""
        if not self._kind or self._total <= 0:
            return ""
        return f"{SHORT_LABEL[self._kind]} {self.remaining} left"


if __name__ == "__main__":
    # Standalone self-check with a trimmed real log (workflow run 2026-10-09).
    _lines = [
        "[RUNNING] Date Fill (REGULAR, discharge date)",
        "[workflow] Date Fill (REGULAR, discharge date): python -m x",
        "Mode: LIVE",
        "Claims: 3",
        "[LIVE] processing COLOBONG, JOSEPH DAVE GONZALES | 000000000010920",
        "[LIVE] processing CORMINAL, SAMANTHA BLANCQUERA | 000000000002842",
        "[FAILED] Date Fill (REGULAR, discharge date) - exit code non-zero",
        "[workflow] Date Fill (REGULAR, discharge date) failed - continuing",
        "[RUNNING] Final Bill (HBSys Billing -> Final)",
        "[LIVE] 3 patient folder(s) under C:\\claims_bot\\output, "
        "1 pending (NO FINAL BILL), 2 skipped",
        "[LIVE] (1/1) DAYAG, VIC ERNESTO JR TAYABAN - 000000000001450",
        "[DONE] Final Bill (HBSys Billing -> Final)",
    ]
    tracker = WorkflowProgress()
    for _line in _lines:
        tracker.feed(_line)
        _shown = tracker.label or "(no count)"
        print(f"{_line[:58]:58} -> kind={tracker.kind or '-':9} {_shown}")
    print("after DONE ->", repr(tracker.label))
    assert tracker.label == "", "a finished node must not keep a count"
    print("OK")