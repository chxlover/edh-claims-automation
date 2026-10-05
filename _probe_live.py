"""Temp: probe what close/clear do headless (returns False with a note)."""
import sys

sys.path.insert(0, "C:/claims_bot")

from core.agent import final_bill_actions as fb  # noqa: E402

notes = []
print("close:", fb.close_billing_form(log_fn=notes.append))
print("notes:", notes)
notes2 = []
print("clear:", fb.clear_blocking_popups(log_fn=notes2.append))
print("notes2:", notes2)
print("forms:", fb.list_open_forms())
print("admit popup:", fb.find_admission_history())
