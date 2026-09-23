"""Claim Attachment Checklist editor (Preferences).

One switch per document:

    CHECKED   -> the document is required by the Claims Checker AND is
                 uploaded to HBSys by the Claim Attachments uploader.
    UNCHECKED -> the document is NOT required by the Claims Checker AND its
                 files are MOVED (never deleted) out of the patient folder
                 into claims_checker_results\\_upload_backup\\<patient>\\
                 BEFORE the uploader attaches anything, so they are never
                 uploaded to HBSys.

Opened from Preferences -> Claim Attachments -> Edit Claim Attachment
Checklist.  It edits claim_attachment_profile.json (local configuration)
only: the Claims Checker, the doc-type vocabulary of the Claim Attachments
grid and the uploader exclusion step all read the profile through
core/claim_attachment_profile.py.  "Restore Defaults" deletes the file and
brings back the exact hardcoded behavior.

Keyboard: Space, Enter or double-click toggles the selected document(s).
Custom documents (documents PhilHealth adds later) can be added, removed and
their HBSys doc type renamed without touching any Python file.
"""

from __future__ import annotations

import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Dict, List, Optional, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.claim_attachment_profile import (
    DEFAULT_DOCS,
    DEFAULT_SEARCH_ROOTS,
    PROFILE_FILE,
    backup_root_for,
    get_profile,
    last_profile_error,
    restore_all_excluded,
    restore_defaults,
    save_overrides,
    validate_doctype,
    validate_overrides,
)

CHECKED = "[x]"
UNCHECKED = "[ ]"
_CODE_RE = re.compile(r"^[A-Za-z0-9]{2,12}$")
_SUFFIX_RE = re.compile(r"^[A-Za-z0-9]{2,8}$")


class ClaimAttachmentChecklistDialog:
    """Modal editor for claim_attachment_profile.json."""

    def __init__(self, parent: tk.Misc, log_callback=None) -> None:
        self.parent = parent
        self.log_callback = log_callback or (lambda _message: None)
        self.saved = False
        self.working, self.load_error = self._load_working()
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Claim Attachment Checklist")
        try:
            self.dialog.transient(parent.winfo_toplevel())
        except Exception:
            pass
        self.dialog.geometry("900x640")
        self._build_ui()
        self._refresh_table()
        self.dialog.grab_set()

    # ------------------------------------------------------------ helpers

    def _log(self, message: str) -> None:
        try:
            self.log_callback(str(message))
        except Exception:
            pass

    def _load_working(self) -> Tuple[Dict[str, Dict[str, Any]], str]:
        """Effective checklist (built-ins then custom) + profile problem text."""
        try:
            profile = get_profile()
            error = last_profile_error()
        except Exception as exc:  # pragma: no cover - defensive
            profile = {}
            error = f"profile unreadable: {exc}"
        if not profile:
            profile = {code: dict(entry) for code, entry in DEFAULT_DOCS.items()}
        working: Dict[str, Dict[str, Any]] = {}
        for code, entry in profile.items():
            row = dict(entry)
            row["enabled"] = bool(row.get("enabled", True))
            row["hbsys_doctype"] = str(row.get("hbsys_doctype") or "").upper()
            row["suffix"] = str(row.get("suffix") or "").upper()
            row["kind"] = (
                "xml" if str(row.get("kind", "pdf")).lower() == "xml" else "pdf"
            )
            working[code] = row
        return working, error

    def _ordered_codes(self) -> List[str]:
        built_in = [code for code in DEFAULT_DOCS if code in self.working]
        custom = sorted(code for code in self.working if code not in DEFAULT_DOCS)
        return built_in + custom

    def _row_values(self) -> Dict[str, Tuple[str, ...]]:
        """{code: table row values} - also used by the standalone test."""
        rows: Dict[str, Tuple[str, ...]] = {}
        for item in self.tree.get_children():
            values = tuple(self.tree.item(item, "values"))
            rows[str(values[1])] = values
        return rows

    def _selected_codes(self) -> List[str]:
        return [str(self.tree.item(item, "values")[1]) for item in self.tree.selection()]

    def _status_text(self) -> str:
        enabled = sum(1 for e in self.working.values() if e.get("enabled", True))
        text = (
            f"{enabled} of {len(self.working)} documents checked = required and "
            f"uploaded.   Profile file: {PROFILE_FILE}"
        )
        if self.load_error:
            text += f"   [profile problem: {self.load_error}]"
        return text

    # ------------------------------------------------------------ main ui

    def _build_ui(self) -> None:
        header = ttk.Frame(self.dialog, padding=(12, 12, 12, 0))
        header.pack(fill="x")
        ttk.Label(
            header,
            justify="left",
            text=(
                "CHECKED = required by the Claims Checker AND uploaded to HBSys.\n"
                "UNCHECKED = not required; its files are moved to\n"
                "claims_checker_results\\_upload_backup\\<patient>\\ before the "
                "upload, so they are never uploaded."
            ),
        ).pack(anchor="w")
        ttk.Label(
            header,
            text="Space, Enter or double-click toggles the selected document(s).",
            foreground="#64748B",
        ).pack(anchor="w", pady=(4, 0))

        table_frame = ttk.Frame(self.dialog, padding=(12, 8, 12, 0))
        table_frame.pack(fill="both", expand=True)
        columns = ("required", "code", "kind", "suffix", "doctype", "origin")
        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="extended"
        )
        headings = (
            ("required", "Required", 80, "center"),
            ("code", "Document", 130, "w"),
            ("kind", "Type", 60, "center"),
            ("suffix", "Filename suffix", 140, "w"),
            ("doctype", "HBSys doc type", 150, "w"),
            ("origin", "Origin", 90, "w"),
        )
        for key, label, width, anchor in headings:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=anchor)
        scroll = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<space>", lambda _event: self._toggle_selected())
        self.tree.bind("<Return>", lambda _event: self._toggle_selected())
        self.tree.bind("<Double-1>", lambda _event: self._toggle_selected())

        row1 = ttk.Frame(self.dialog, padding=(12, 8, 12, 0))
        row1.pack(fill="x")
        ttk.Button(
            row1, text="Toggle Required", command=self._toggle_selected
        ).pack(side="left")
        ttk.Button(
            row1, text="Check All", command=lambda: self._set_all(True)
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            row1, text="Uncheck All", command=lambda: self._set_all(False)
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            row1, text="Edit HBSys Doc Type...", command=self._edit_doctype
        ).pack(side="left", padx=(18, 0))
        ttk.Button(
            row1, text="Add Custom Document...", command=self._add_custom_dialog
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            row1, text="Remove Custom", command=self._remove_custom
        ).pack(side="left", padx=(6, 0))

        row2 = ttk.Frame(self.dialog, padding=(12, 6, 12, 0))
        row2.pack(fill="x")
        ttk.Button(
            row2, text="Restore Excluded Files...", command=self._restore_excluded_files
        ).pack(side="left")
        ttk.Button(
            row2, text="Restore Defaults", command=self._restore_defaults
        ).pack(side="left", padx=(6, 0))

        self.status_var = tk.StringVar(value=self._status_text())
        ttk.Label(
            self.dialog,
            textvariable=self.status_var,
            foreground="#64748B",
            justify="left",
            wraplength=850,
            padding=(12, 10, 12, 0),
        ).pack(anchor="w", fill="x")

        footer = ttk.Frame(self.dialog, padding=(12, 8, 12, 12))
        footer.pack(fill="x")
        ttk.Button(
            footer, text="Save", command=self._on_save, style="Accent.TButton"
        ).pack(side="right")
        ttk.Button(footer, text="Cancel", command=self.dialog.destroy).pack(
            side="right", padx=(0, 8)
        )

    # ------------------------------------------------------------ actions

    def _refresh_table(self) -> None:
        keep = set(self._selected_codes())
        self.tree.delete(*self.tree.get_children())
        for code in self._ordered_codes():
            entry = self.working[code]
            self.tree.insert(
                "",
                "end",
                values=(
                    CHECKED if entry.get("enabled", True) else UNCHECKED,
                    code,
                    "XML" if entry.get("kind") == "xml" else "PDF",
                    entry.get("suffix", ""),
                    entry.get("hbsys_doctype", ""),
                    "custom" if code not in DEFAULT_DOCS else "built-in",
                ),
            )
        for item in self.tree.get_children():
            if str(self.tree.item(item, "values")[1]) in keep:
                self.tree.selection_add(item)
        if hasattr(self, "status_var"):
            self.status_var.set(self._status_text())

    def _set_all(self, value: bool) -> None:
        for entry in self.working.values():
            entry["enabled"] = bool(value)
        self._refresh_table()

    def _set_enabled(self, codes: List[str], value: bool) -> None:
        for code in codes:
            if code in self.working:
                self.working[code]["enabled"] = bool(value)
        self._refresh_table()

    def _toggle_selected(self) -> None:
        codes = [c for c in self._selected_codes() if c in self.working]
        if not codes:
            return
        # Unchecked rows win: toggling a mixed selection turns everything on.
        turn_on = not all(self.working[code].get("enabled", True) for code in codes)
        self._set_enabled(codes, turn_on)

    def _set_doctype(self, code: str, value: str) -> str:
        """Validate + apply one HBSys doc type.  Returns '' or an error text."""
        if code not in self.working:
            return f"Unknown document {code}."
        value = str(value or "").strip().upper()
        error = validate_doctype(value)
        if error:
            return error
        self.working[code]["hbsys_doctype"] = value
        self._refresh_table()
        return ""

    def _edit_doctype(self) -> None:
        codes = self._selected_codes()
        if len(codes) != 1:
            messagebox.showinfo(
                "Claim Attachment Checklist",
                "Select exactly one document first.",
                parent=self.dialog,
            )
            return
        code = codes[0]
        value = simpledialog.askstring(
            "HBSys Doc Type",
            f"HBSys doc type typed into the grid for {code}\n"
            "(2-12 uppercase letters/digits):",
            initialvalue=self.working[code].get("hbsys_doctype", ""),
            parent=self.dialog,
        )
        if value is None:
            return
        error = self._set_doctype(code, value)
        if error:
            messagebox.showwarning(
                "Claim Attachment Checklist", error, parent=self.dialog
            )
            return
        self._log(
            f"[CHECKLIST] {code} HBSys doc type -> "
            f"{value.strip().upper()} (not saved yet)"
        )

    def _add_custom(self, code: str, kind: str, suffix: str, doctype: str) -> str:
        """Validate + add a custom document.  Returns '' or an error text.

        A custom document is added as required (conditional=False) because the
        owner only adds a document that PhilHealth now requires.
        """
        code = str(code or "").strip().upper()
        kind = "xml" if str(kind).lower() == "xml" else "pdf"
        suffix = str(suffix or "").strip().upper() or code
        doctype = str(doctype or "").strip().upper() or code
        if not _CODE_RE.match(code):
            return "Document code must be 2-12 letters or digits (example: XRAY)."
        if code in self.working:
            return f"{code} is already in the checklist."
        if not _SUFFIX_RE.match(suffix):
            return "Filename suffix must be 2-8 letters or digits (example: XRAY)."
        used = {
            str(entry.get("suffix") or "").upper()
            for entry in self.working.values()
        }
        if suffix in used:
            return f"Suffix {suffix} is already used by another document."
        error = validate_doctype(doctype)
        if error:
            return error
        self.working[code] = {
            "kind": kind,
            "suffix": suffix,
            "hbsys_doctype": doctype,
            "conditional": False,
            "enabled": True,
            "custom": True,
        }
        self._refresh_table()
        return ""

    def _add_custom_dialog(self) -> None:
        code = simpledialog.askstring(
            "Add Custom Document",
            "New document code (2-12 letters/digits, example: XRAY):",
            parent=self.dialog,
        )
        if not code:
            return
        is_pdf = messagebox.askyesnocancel(
            "Add Custom Document",
            "Is the new document a PDF file?\n\nYes = PDF        No = XML",
            parent=self.dialog,
        )
        if is_pdf is None:
            return
        suffix = simpledialog.askstring(
            "Add Custom Document",
            "Filename suffix used inside the row path:",
            initialvalue=code.strip().upper(),
            parent=self.dialog,
        )
        if suffix is None:
            return
        doctype = simpledialog.askstring(
            "Add Custom Document",
            "HBSys doc type typed into the grid:",
            initialvalue=code.strip().upper(),
            parent=self.dialog,
        )
        if doctype is None:
            return
        error = self._add_custom(code, "pdf" if is_pdf else "xml", suffix, doctype)
        if error:
            messagebox.showwarning(
                "Claim Attachment Checklist", error, parent=self.dialog
            )
            return
        self._log(
            f"[CHECKLIST] custom document added: {code.strip().upper()} "
            "(not saved yet)"
        )

    def _remove_custom(self) -> None:
        codes = [c for c in self._selected_codes() if c not in DEFAULT_DOCS]
        if not codes:
            messagebox.showinfo(
                "Claim Attachment Checklist",
                "Select a custom document to remove.\n\nBuilt-in documents "
                "cannot be removed - uncheck them instead.",
                parent=self.dialog,
            )
            return
        if not messagebox.askyesno(
            "Claim Attachment Checklist",
            f"Remove custom document(s) {', '.join(codes)}?",
            parent=self.dialog,
        ):
            return
        for code in codes:
            self.working.pop(code, None)
        self._refresh_table()

    # ------------------------------------------------- restore + save

    def _do_restore_defaults(self) -> str:
        """Delete the profile file and reload defaults.  '' on success."""
        error = str(restore_defaults() or "")
        self.working, self.load_error = self._load_working()
        self._refresh_table()
        return error

    def _restore_defaults(self) -> None:
        if not messagebox.askyesno(
            "Claim Attachment Checklist",
            "Restore the built-in default checklist?\n\nEvery document becomes "
            "checked and required again, and the profile file is deleted.",
            parent=self.dialog,
        ):
            return
        error = self._do_restore_defaults()
        if error:
            messagebox.showwarning(
                "Claim Attachment Checklist",
                f"Not restored: {error}",
                parent=self.dialog,
            )
            return
        self._log("[CHECKLIST] default checklist restored (profile file deleted)")

    def _do_restore_excluded(
        self, backup_root=None, search_roots=None
    ) -> Dict[str, List[str]]:
        """Move backed-up files back into their patient folders."""
        root = (
            Path(backup_root)
            if backup_root is not None
            else backup_root_for(DEFAULT_SEARCH_ROOTS[0])
        )
        roots = (
            tuple(search_roots) if search_roots is not None else DEFAULT_SEARCH_ROOTS
        )
        return restore_all_excluded(root, roots)

    def _restore_excluded_files(self) -> None:
        if not messagebox.askyesno(
            "Claim Attachment Checklist",
            "Move every excluded file back into its patient folder "
            "(READY first, then READY_ARCHIVED)?",
            parent=self.dialog,
        ):
            return
        try:
            results = self._do_restore_excluded()
        except Exception as exc:
            messagebox.showerror(
                "Claim Attachment Checklist",
                f"Restore failed: {exc}",
                parent=self.dialog,
            )
            return
        if not results:
            messagebox.showinfo(
                "Claim Attachment Checklist",
                "No excluded files to restore.",
                parent=self.dialog,
            )
            return
        lines = [
            f"{patient}: {', '.join(files)}" for patient, files in results.items()
        ]
        messagebox.showinfo(
            "Claim Attachment Checklist",
            "Restored:\n\n" + "\n".join(lines[:20]),
            parent=self.dialog,
        )
        self._log(
            f"[CHECKLIST] restored excluded files for {len(results)} patient folder(s)"
        )

    def _on_save(self) -> None:
        """Write only deltas: built-ins store changed fields, custom as full."""
        docs_delta: Dict[str, Dict[str, Any]] = {}
        custom: Dict[str, Dict[str, Any]] = {}
        for code in self._ordered_codes():
            entry = self.working[code]
            enabled = bool(entry.get("enabled", True))
            doctype = str(entry.get("hbsys_doctype") or "").upper()
            if code in DEFAULT_DOCS:
                default = DEFAULT_DOCS[code]
                delta: Dict[str, Any] = {}
                if enabled != bool(default.get("enabled", True)):
                    delta["enabled"] = enabled
                if doctype != str(default.get("hbsys_doctype") or "").upper():
                    delta["hbsys_doctype"] = doctype
                if delta:
                    docs_delta[code] = delta
            else:
                custom[code] = {
                    "kind": "xml" if entry.get("kind") == "xml" else "pdf",
                    "suffix": str(entry.get("suffix") or code).upper(),
                    "hbsys_doctype": doctype or code,
                    "conditional": bool(entry.get("conditional", False)),
                    "enabled": enabled,
                }

        cleaned, error = validate_overrides(
            {"docs": docs_delta, "custom_docs": custom}
        )
        if error:
            messagebox.showwarning(
                "Claim Attachment Checklist",
                f"Not saved: {error}",
                parent=self.dialog,
            )
            return
        error = str(save_overrides(cleaned) or "")
        if error:
            messagebox.showwarning(
                "Claim Attachment Checklist",
                f"Saved with problems: {error}",
                parent=self.dialog,
            )
        self.saved = True
        enabled = sum(1 for e in self.working.values() if e.get("enabled", True))
        self._log(
            f"[CHECKLIST] saved: {enabled} of {len(self.working)} documents checked"
        )
        self.dialog.destroy()


if __name__ == "__main__":
    import tempfile
    from typing import Dict as _Dict
    from typing import List as _List

    from core import claim_attachment_profile as profile

    failures = [0]

    def check(label: str, ok: bool) -> None:
        failures[0] += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")

    real_profile_file = profile.PROFILE_FILE
    tmp_root = Path(tempfile.mkdtemp())
    profile.PROFILE_FILE = tmp_root / "claim_attachment_profile.json"
    profile._invalidate_cache()

    root = tk.Tk()
    root.withdraw()
    messages: _List[str] = []

    # -- 1. defaults: every document checked, nothing saved yet -----------
    dialog = ClaimAttachmentChecklistDialog(root, log_callback=messages.append)
    dialog.dialog.update_idletasks()
    check("lists every default document", len(dialog.working) == len(profile.DEFAULT_DOCS))
    check(
        "every default document is checked",
        all(e.get("enabled", True) for e in dialog.working.values()),
    )
    check("no profile file exists yet", not profile.PROFILE_FILE.is_file())
    rows = dialog._row_values()
    check("CSF row shows checked", rows["CSF"][0] == CHECKED)
    check("SOA1 maps to HBSys doc type SOA", rows["SOA1"][4] == "SOA")
    check("eSOA listed as XML", rows["eSOA"][2] == "XML")
    check("built-ins are labelled", rows["CSF"][5] == "built-in")

    # -- 2. uncheck two documents, then Save ------------------------------
    dialog._set_enabled(["CSF", "eSOA"], False)
    rows = dialog._row_values()
    check(
        "toggle writes back to the table",
        rows["CSF"][0] == UNCHECKED and rows["eSOA"][0] == UNCHECKED,
    )
    dialog._on_save()
    check("save sets the saved flag", dialog.saved)
    check("disable is stored as a delta", profile.is_enabled("CSF") is False)
    check("other documents stay enabled", profile.is_enabled("COE") is True)
    check(
        "disabled CSF leaves the doc type map",
        "CSF" not in profile.get_doctype_maps()[0],
    )
    check(
        "disabled eSOA leaves the XML map",
        "ESOA" not in profile.get_doctype_maps()[1],
    )
    check(
        "disabled documents are listed as excluded",
        {"CSF", "eSOA"} <= set(profile.get_excluded_docs()),
    )

    # -- 3. reopen: saved state + custom document validation --------------
    dialog = ClaimAttachmentChecklistDialog(root, log_callback=messages.append)
    check("reopen shows the saved state", dialog._row_values()["CSF"][0] == UNCHECKED)
    check(
        "custom validation rejects a short code",
        dialog._add_custom("X", "pdf", "X", "X") != "",
    )
    check(
        "custom validation rejects a used suffix",
        dialog._add_custom("XRAY", "pdf", "CSF", "XRAY") != "",
    )
    check(
        "custom validation rejects a bad doc type",
        dialog._add_custom("XRAY", "pdf", "XR", "bad type!") != "",
    )
    check(
        "custom document accepted", dialog._add_custom("XRAY", "pdf", "XR", "XRAY") == ""
    )
    check("custom document appears", dialog._row_values()["XRAY"][5] == "custom")
    check(
        "doc type can be renamed",
        dialog._set_doctype("CSF", "CSF2") == ""
        and dialog._row_values()["CSF"][4] == "CSF2",
    )
    dialog._set_doctype("CSF", "CSF")
    dialog._on_save()
    check("custom suffix in the doc type map", "XR" in profile.get_doctype_maps()[0])
    check(
        "custom document is required",
        "XRAY" in profile.get_required_base_docs(),
    )

    # -- 4. restore defaults ---------------------------------------------
    dialog = ClaimAttachmentChecklistDialog(root, log_callback=messages.append)
    check(
        "restore defaults clean",
        dialog._do_restore_defaults() == "" and not profile.PROFILE_FILE.is_file(),
    )
    check(
        "restore defaults re-checks every document",
        all(row[0] == CHECKED for row in dialog._row_values().values()),
    )
    check("restore defaults drops custom documents", "XRAY" not in profile.get_profile())

    # -- 5. restore excluded files (temp READY tree) ----------------------
    ready = tmp_root / "claims_checker_results" / "READY"
    patient = ready / "DELA CRUZ, JUAN - 000000000000001 - ADM20260801_DIS20260805"
    patient.mkdir(parents=True)
    (patient / "CSF.pdf").write_text("x", encoding="utf-8")
    backup = profile.backup_root_for(ready)
    excluded_dir = backup / patient.name
    excluded_dir.mkdir(parents=True)
    (excluded_dir / "SOA1.pdf").write_text("x", encoding="utf-8")
    results: _Dict[str, _List[str]] = dialog._do_restore_excluded(backup, (ready,))
    check("excluded file moved back", (patient / "SOA1.pdf").is_file())
    check(
        "restore reports the moved file",
        results.get(patient.name) == ["SOA1.pdf"],
    )

    dialog.dialog.destroy()
    try:
        root.destroy()
    except Exception:
        pass
    profile.PROFILE_FILE = real_profile_file
    profile._invalidate_cache()
    print("RESULT:", "PASSED" if failures[0] == 0 else f"{failures[0]} FAILURE(S)")
    raise SystemExit(0 if failures[0] == 0 else 1)
