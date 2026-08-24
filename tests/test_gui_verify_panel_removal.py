"""Standalone GUI test: Quick Actions layout of the main Claims GUI.

Instantiates the real EDHClaimsGUI (window is withdrawn so it does not
flash on screen), then checks:

  1. SCRIPT_CONFIG no longer contains the "verification_panel" or
     "date_fill_hbsys" entries, and still contains
     "date_fill_hbsys_testing".
  2. EDHClaimsGUI no longer defines the open_verification_panel() method.
  3. No widget anywhere in the GUI has the text "Verify Panel" or an
     exact "Date Fill" button (the retained button is
     "Date Fill ABTC/Regular").
  4. The notebook tabs are intact (nothing else was removed).
  5. Quick Actions grid: every expected button is present, sits on the
     expected row/column, and the grid has no duplicate cells or gaps.

Run from the project root:

    python tests/test_gui_verify_panel_removal.py
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import edh_claims_gui_XML_COPY_BUTTON as gui_module

FAILURES = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def find_widget_by_text(root, text):
    """Depth-first search for the first widget whose 'text' option equals text."""

    def walk(widget):
        try:
            if widget.cget("text") == text:
                return widget
        except Exception:
            pass
        for child in widget.winfo_children():
            result = walk(child)
            if result is not None:
                return result
        return None

    return walk(root)


def collect_button_texts(root):
    texts = []

    def walk(widget):
        try:
            if widget.cget("text"):
                texts.append(str(widget.cget("text")))
        except Exception:
            pass
        for child in widget.winfo_children():
            walk(child)

    walk(root)
    return texts


def main():
    print("=== Quick Actions Layout GUI Test ===\n")

    # 1. SCRIPT_CONFIG entries.
    check(
        "SCRIPT_CONFIG has no 'verification_panel' entry",
        "verification_panel" not in gui_module.SCRIPT_CONFIG,
        "present" if "verification_panel" in gui_module.SCRIPT_CONFIG else "absent",
    )
    check(
        "SCRIPT_CONFIG has no 'date_fill_hbsys' entry",
        "date_fill_hbsys" not in gui_module.SCRIPT_CONFIG,
        "present" if "date_fill_hbsys" in gui_module.SCRIPT_CONFIG else "absent",
    )
    check(
        "SCRIPT_CONFIG keeps 'date_fill_hbsys_testing' entry",
        "date_fill_hbsys_testing" in gui_module.SCRIPT_CONFIG,
    )

    # 2. The class must no longer define open_verification_panel.
    check(
        "EDHClaimsGUI has no open_verification_panel method",
        not hasattr(gui_module.EDHClaimsGUI, "open_verification_panel"),
    )

    # 3. Instantiate the real GUI with the window withdrawn.
    print("\nInstantiating EDHClaimsGUI (window withdrawn)...")
    app = gui_module.EDHClaimsGUI()
    app.withdraw()
    app.update_idletasks()

    try:
        # 3a. No widget may have the text "Verify Panel".
        all_texts = collect_button_texts(app)
        check(
            "No 'Verify Panel' button/text exists in the GUI",
            "Verify Panel" not in all_texts,
            "found" if "Verify Panel" in all_texts else "not found",
        )
        # No widget may have the exact text "Date Fill" (only the
        # "Date Fill ABTC/Regular" replacement is allowed). Exact match
        # only, so the substring inside "Date Fill ABTC/Regular" is fine.
        check(
            "No 'Date Fill' (exact) button exists in the GUI",
            all(text != "Date Fill" for text in all_texts),
            "found" if any(text == "Date Fill" for text in all_texts) else "not found",
        )
        # The replacement button must exist.
        check(
            "'Date Fill ABTC/Regular' button exists",
            "Date Fill ABTC/Regular" in all_texts,
        )

        # 3b. Notebook tabs are intact.
        tabs = [app.notebook.tab(tab_id, "text") for tab_id in app.notebook.tabs()]
        expected_tabs = [
            "Main Dashboard",
            "Folders",
            "Missing Date Fill Batches",
            "Doctor Manager",
            "PDF E-Sign",
            "PDF Compress",
            "PDF Split",
            "Preferences",
            "About",
        ]
        check("Notebook tabs intact", tabs == expected_tabs, f"got {tabs}")

        # 3c. Quick Actions grid checks.
        # Locate the quick_actions frame via the "PDF E-Sign" quick button.
        esign_button = find_widget_by_text(app, "PDF E-Sign")
        if esign_button is None:
            check("Quick Action 'PDF E-Sign' exists", False, "not found")
            return
        quick_actions = esign_button.master

        expected_cells = {
            "PDF E-Sign": (2, 0),
            "Date Fill ABTC/Regular": (2, 1),
            "XML Clicker": (3, 0),
            "Copy XML": (3, 1),
            "Check Missing": (4, 0),
            "Fees Check": (4, 1),
            "Recheck INC": (5, 0),
            "PDF Compress": (5, 1),
            "PDF Split": (6, 0),
        }

        occupied = {}
        duplicates = []

        for text, (row, col) in expected_cells.items():
            button = find_widget_by_text(quick_actions, text)
            check(f"Quick Action '{text}' exists", button is not None)
            if button is None:
                continue
            info = button.grid_info()
            position = (int(info["row"]), int(info["column"]))
            check(
                f"Quick Action '{text}' at row {row} col {col}",
                position == (row, col),
                f"got {position}",
            )
            if position in occupied:
                duplicates.append((position, occupied[position], text))
            else:
                occupied[position] = text

        check(
            "No duplicate grid cells in Quick Actions",
            not duplicates,
            str(duplicates) if duplicates else "",
        )

        # Gaps: every quick-action row must have both columns filled.
        for row in range(2, 6):
            for col in range(2):
                check(
                    f"Quick Actions cell ({row}, {col}) occupied",
                    (row, col) in occupied,
                )
    finally:
        app.destroy()

    print()
    if FAILURES:
        print(f"RESULT: FAILED ({len(FAILURES)} check(s) failed)")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
