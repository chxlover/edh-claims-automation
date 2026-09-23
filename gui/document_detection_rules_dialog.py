"""Configurable document detection rules editor.

Opened from Preferences -> Document Detection -> Edit Document Detection
Rules.  It edits document_detection_rules.json (local configuration) only -
the hardcoded detector in the production engine is never modified here.

Per-type editor fields: enabled, priority, min_hits, keywords, strong
keywords, aliases and page rules (page number -> enabled / min_hits /
keywords / strong).  Remaining advanced fields (suppress_if, fuzzy, custom
keys) stay untouched by the simple fields and remain editable as JSON (type
level and per page via "Adv..."), so round-tripping defaults never loses data.
"""

from __future__ import annotations

import json
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.document_detection_rules import (
    SCHEMA_VERSION,
    default_rules,
    load_rules,
    restore_defaults,
    save_rules,
)

_TYPE_CODE_RE = re.compile(r"^[A-Za-z0-9_]{2,24}$")
_MANAGED_RULE_KEYS = ("enabled", "priority", "min_hits", "keywords", "strong", "aliases", "page_rules")


class DocumentDetectionRulesDialog:
    """Modal editor for document_detection_rules.json."""

    def __init__(self, parent: tk.Misc, log_callback=None) -> None:
        self.parent = parent
        self.log_callback = log_callback or (lambda _message: None)
        self.saved = False
        self.working, self.load_error = self._load_working()
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Document Detection Rules")
        try:
            self.dialog.transient(parent.winfo_toplevel())
        except Exception:
            pass
        self.dialog.geometry("780x600")
        self._build_ui()
        self._refresh_table()
        self.dialog.grab_set()

    # ------------------------------------------------------------ helpers

    def _log(self, message: str) -> None:
        try:
            self.log_callback(str(message))
        except Exception:
            pass

    def _load_working(self):
        document_types, error = load_rules()
        if document_types is None:
            return default_rules(), error
        return document_types, error

    def _selected_type(self) -> Optional[str]:
        selection = self.tree.selection()
        if not selection:
            return None
        return self.tree.item(selection[0], "values")[0]

    # ------------------------------------------------------------ main ui

    def _build_ui(self) -> None:
        header = ttk.Frame(self.dialog, padding=(12, 12, 12, 0))
        header.pack(fill="x")
        ttk.Label(
            header,
            text="DOCUMENT DETECTION RULES",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "Used ONLY when Preferences > Document Detection Rules "
                "Configurable is ON.  With the switch OFF the built-in "
                "hardcoded detection runs and these rules have no effect."
            ),
            foreground="#64748B",
            wraplength=720,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))
        if self.load_error:
            ttk.Label(
                header,
                text="Configuration warning: " + self.load_error,
                foreground="#DC2626",
                wraplength=720,
                justify="left",
            ).pack(anchor="w", pady=(4, 0))

        table_frame = ttk.LabelFrame(
            self.dialog, text="Document Types", padding=10
        )
        table_frame.pack(fill="both", expand=True, padx=12, pady=(10, 0))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            table_frame,
            columns=("type", "enabled", "priority", "pages", "keywords"),
            show="headings",
            height=12,
        )
        self.tree.heading("type", text="Type")
        self.tree.heading("enabled", text="Enabled")
        self.tree.heading("priority", text="Priority")
        self.tree.heading("pages", text="Pages")
        self.tree.heading("keywords", text="Keywords")
        self.tree.column("type", width=140, anchor="w")
        self.tree.column("enabled", width=80, anchor="center")
        self.tree.column("priority", width=80, anchor="center")
        self.tree.column("pages", width=110, anchor="center")
        self.tree.column("keywords", width=70, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.tree.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(table_frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="Add", command=self._add).pack(side="left")
        ttk.Button(buttons, text="Edit", command=self._edit).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Delete", command=self._delete).pack(side="left", padx=(6, 0))
        ttk.Button(
            buttons, text="Restore Defaults", command=self._restore_defaults
        ).pack(side="left", padx=(6, 0))

        footer = ttk.Frame(self.dialog, padding=12)
        footer.pack(fill="x", side="bottom")
        ttk.Button(
            footer, text="Save", command=self._save, style="Accent.TButton"
        ).pack(side="right")
        ttk.Button(footer, text="Cancel", command=self._cancel).pack(
            side="right", padx=(0, 8)
        )

    def _refresh_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for doc_type, rule in self.working.items():
            if not isinstance(rule, dict):
                continue
            enabled = "YES" if rule.get("enabled", True) else "NO"
            keyword_count = len(rule.get("keywords") or [])
            page_rules = rule.get("page_rules") or {}
            for page_spec in page_rules.values():
                if isinstance(page_spec, dict):
                    keyword_count += len(page_spec.get("keywords") or [])
            if page_rules:
                page_keys = sorted(
                    (str(key) for key in page_rules),
                    key=lambda k: (k == "single", int(k) if k.isdigit() else 0),
                )
                pages_label = ", ".join(page_keys)
            else:
                pages_label = "single-page"
            self.tree.insert(
                "",
                "end",
                values=(
                    doc_type,
                    enabled,
                    rule.get("priority", 50),
                    pages_label,
                    keyword_count,
                ),
            )

    # ------------------------------------------------------------ actions

    def _add(self) -> None:
        code = simpledialog.askstring(
            "Add Document Type",
            "Document code (2-24 letters/digits/underscore, e.g. NDR):",
            parent=self.dialog,
        )
        if not code:
            return
        code = code.strip().upper()
        if not _TYPE_CODE_RE.match(code):
            messagebox.showerror(
                "Add Document Type", f"Invalid document code: {code}", parent=self.dialog
            )
            return
        if code in self.working:
            messagebox.showerror(
                "Add Document Type", f"{code} already exists", parent=self.dialog
            )
            return
        self._open_editor(code, is_new=True)

    def _edit(self) -> None:
        doc_type = self._selected_type()
        if not doc_type:
            messagebox.showinfo("Edit", "Select a document type first.", parent=self.dialog)
            return
        self._open_editor(doc_type, is_new=False)

    def _delete(self) -> None:
        doc_type = self._selected_type()
        if not doc_type:
            messagebox.showinfo("Delete", "Select a document type first.", parent=self.dialog)
            return
        if not messagebox.askyesno(
            "Delete Document Type",
            f"Delete {doc_type} from the configurable rules?",
            parent=self.dialog,
        ):
            return
        self.working.pop(doc_type, None)
        self._refresh_table()
        self._log(f"Document detection rule deleted: {doc_type} (not saved yet)")

    def _restore_defaults(self) -> None:
        if not messagebox.askyesno(
            "Restore Defaults",
            "Replace ALL configurable rules with the built-in defaults\n"
            "(derived from the hardcoded detection)?",
            parent=self.dialog,
        ):
            return
        self.working = default_rules()
        self._refresh_table()
        self._log("Document detection rules restored to defaults (not saved yet)")

    def _save(self) -> None:
        message = save_rules(self.working)
        if message:
            messagebox.showwarning(
                "Save Document Detection Rules", message, parent=self.dialog
            )
        self.saved = True
        self._log("Document detection rules saved to document_detection_rules.json")
        self.dialog.destroy()

    def _cancel(self) -> None:
        self.dialog.destroy()

    # ------------------------------------------------------- type editor

    def _open_editor(self, doc_type: str, is_new: bool) -> None:
        work = json.loads(json.dumps(self.working.get(doc_type, {})))
        work.setdefault("enabled", True)
        work.setdefault("priority", 50)
        work.setdefault("keywords", [])
        work.setdefault("aliases", [])
        work.setdefault("page_rules", {})
        advanced = {
            key: value
            for key, value in work.items()
            if key not in _MANAGED_RULE_KEYS
        }

        editor = tk.Toplevel(self.dialog)
        editor.title(f"Document Detection Rule - {doc_type}")
        editor.transient(self.dialog)
        editor.geometry("720x860")

        # Packed FIRST (side="bottom") so the footer always stays visible;
        # the page-rule button bar is packed second so it stays above it.
        footer = ttk.Frame(editor, padding=10)
        footer.pack(fill="x", side="bottom")

        page_buttons = ttk.Frame(editor, padding=(10, 4))
        page_buttons.pack(fill="x", side="bottom")
        ttk.Button(
            page_buttons,
            text="Add Page Rule",
            command=lambda: _add_page_row(),
        ).pack(side="left")
        ttk.Label(
            page_buttons,
            text='Page key: 1, 2, ... or "single" for the complete form.',
            foreground="#64748B",
        ).pack(side="left", padx=(8, 0))

        top = ttk.Frame(editor, padding=(10, 10, 10, 0))
        top.pack(fill="x")
        ttk.Label(top, text="Document Code:").grid(row=0, column=0, sticky="w")
        code_var = tk.StringVar(value=doc_type)
        code_entry = ttk.Entry(top, textvariable=code_var, width=24)
        code_entry.grid(row=0, column=1, sticky="w", padx=(8, 0))
        if not is_new:
            code_entry.configure(state="readonly")
        enabled_var = tk.BooleanVar(value=bool(work.get("enabled", True)))
        ttk.Checkbutton(top, text="Enabled", variable=enabled_var).grid(
            row=1, column=1, sticky="w", pady=(6, 0)
        )
        ttk.Label(top, text="Priority:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        priority_var = tk.StringVar(value=str(work.get("priority", 50)))
        ttk.Spinbox(
            top, from_=-1000, to=1000, textvariable=priority_var, width=8
        ).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(
            top,
            text="Higher priority is evaluated first (defaults mirror the hardcoded order).",
            foreground="#64748B",
        ).grid(row=2, column=2, sticky="w", padx=(10, 0))
        ttk.Label(top, text="Min hits:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        min_hits_var = tk.StringVar(value=str(work.get("min_hits", 1)))
        ttk.Spinbox(
            top, from_=1, to=99, textvariable=min_hits_var, width=8
        ).grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(
            top,
            text="Keyword hits required on a single-page scan (default 1, e.g. ANR = 2).",
            foreground="#64748B",
        ).grid(row=3, column=2, sticky="w", padx=(10, 0))

        def _list_text(frame, label, values, height):
            ttk.Label(frame, text=label).pack(anchor="w", padx=10, pady=(6, 0))
            text = tk.Text(frame, height=height, width=70, wrap="none")
            text.pack(fill="x", padx=10)
            text.insert("1.0", "\n".join(values))
            return text

        keywords_text = _list_text(editor, "Keywords (one per line):", work.get("keywords") or [], 6)
        strong_text = _list_text(
            editor,
            "Strong keywords (one per line, instant match when found):",
            work.get("strong") or [],
            3,
        )
        aliases_text = _list_text(editor, "Aliases (one per line, optional):", work.get("aliases") or [], 2)

        advanced_frame = ttk.LabelFrame(
            editor,
            text="Advanced (JSON) - suppress_if, fuzzy, custom keys",
            padding=6,
        )
        advanced_frame.pack(fill="x", padx=10, pady=(8, 0))
        advanced_text = tk.Text(advanced_frame, height=5, width=70, wrap="none")
        advanced_text.pack(fill="x")
        advanced_text.insert("1.0", json.dumps(advanced, indent=2, ensure_ascii=False))

        def _open_json_editor(title: str, data: Dict[str, Any]) -> None:
            win = tk.Toplevel(editor)
            win.title(title)
            win.transient(editor)
            win.geometry("460x320")
            json_text = tk.Text(win, wrap="none")
            json_text.pack(fill="both", expand=True, padx=8, pady=(8, 0))
            json_text.insert("1.0", json.dumps(data, indent=2, ensure_ascii=False))
            json_buttons = ttk.Frame(win, padding=8)
            json_buttons.pack(fill="x")


            def _apply_json() -> None:
                raw = json_text.get("1.0", "end").strip() or "{}"
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as exc:
                    messagebox.showerror(
                        "Advanced JSON", f"Invalid JSON: {exc}", parent=win
                    )
                    return
                if not isinstance(parsed, dict):
                    messagebox.showerror(
                        "Advanced JSON",
                        "Value must be a JSON object { ... }.",
                        parent=win,
                    )
                    return
                data.clear()
                data.update(parsed)
                win.destroy()

            ttk.Button(json_buttons, text="OK", command=_apply_json).pack(side="right")
            ttk.Button(json_buttons, text="Cancel", command=win.destroy).pack(
                side="right", padx=(0, 8)
            )
            win.grab_set()

        # --- page rules (page number/"single" -> enabled/min_hits/keywords/strong) ---
        pages_frame = ttk.LabelFrame(editor, text="Page Rules (optional)", padding=8)
        pages_frame.pack(fill="both", expand=True, padx=10, pady=(8, 0))

        page_canvas = tk.Canvas(pages_frame, highlightthickness=0, height=130)
        page_scroll = ttk.Scrollbar(
            pages_frame, orient="vertical", command=page_canvas.yview
        )
        page_inner = ttk.Frame(page_canvas)
        page_inner.bind(
            "<Configure>",
            lambda _event: page_canvas.configure(
                scrollregion=page_canvas.bbox("all")
            ),
        )
        page_canvas.create_window((0, 0), window=page_inner, anchor="nw")
        page_canvas.configure(yscrollcommand=page_scroll.set)
        page_canvas.pack(side="left", fill="both", expand=True)
        page_scroll.pack(side="right", fill="y")

        page_rows = []
        page_managed_keys = ("enabled", "min_hits", "keywords", "strong")

        def _add_page_row(
            page_key: str = "", spec: Optional[Dict[str, Any]] = None
        ) -> None:
            spec = spec or {}
            row = ttk.Frame(page_inner)
            row.pack(fill="x", pady=2)
            key_var = tk.StringVar(value=str(page_key))
            ttk.Entry(row, textvariable=key_var, width=6).pack(side="left")
            enabled_page_var = tk.BooleanVar(value=bool(spec.get("enabled", True)))
            ttk.Checkbutton(row, text="On", variable=enabled_page_var).pack(
                side="left", padx=(4, 0)
            )
            ttk.Label(row, text="min_hits:").pack(side="left", padx=(6, 0))
            hits_var = tk.StringVar(value=str(spec.get("min_hits", 1)))
            ttk.Spinbox(
                row, from_=1, to=99, textvariable=hits_var, width=4
            ).pack(side="left")
            ttk.Label(row, text="keywords:").pack(side="left", padx=(6, 0))
            keywords_var = tk.StringVar(
                value=", ".join(str(k) for k in (spec.get("keywords") or []))
            )
            ttk.Entry(row, textvariable=keywords_var, width=24).pack(
                side="left", padx=(4, 0)
            )
            ttk.Label(row, text="strong:").pack(side="left", padx=(6, 0))
            strong_var = tk.StringVar(
                value=", ".join(str(k) for k in (spec.get("strong") or []))
            )
            ttk.Entry(row, textvariable=strong_var, width=20).pack(
                side="left", padx=(4, 0)
            )
            page_advanced: Dict[str, Any] = {
                key: value
                for key, value in spec.items()
                if key not in page_managed_keys
            }

            def _remove(row_frame: tk.Widget = row) -> None:
                row_frame.destroy()
                page_rows[:] = [
                    p for p in page_rows if p["frame"] is not row_frame
                ]

            ttk.Button(
                row,
                text="Adv...",
                width=6,
                command=lambda: _open_json_editor(
                    f'Advanced page rule ({key_var.get() or "?"}) - {doc_type}',
                    page_advanced,
                ),
            ).pack(side="left", padx=(6, 0))
            ttk.Button(row, text="X", width=2, command=_remove).pack(
                side="left", padx=(4, 0)
            )
            page_rows.append(
                {
                    "frame": row,
                    "key": key_var,
                    "enabled": enabled_page_var,
                    "min_hits": hits_var,
                    "keywords": keywords_var,
                    "strong": strong_var,
                    "advanced": page_advanced,
                }
            )

        existing_pages = work.get("page_rules") or {}
        for existing_key in sorted(existing_pages, key=lambda k: (k == "single", int(k) if k.isdigit() else 0)):
            existing_spec = existing_pages.get(existing_key)
            if isinstance(existing_spec, dict):
                _add_page_row(str(existing_key), existing_spec)

        def _text_lines(widget: tk.Text) -> List[str]:
            return [
                line.strip()
                for line in widget.get("1.0", "end").splitlines()
                if line.strip()
            ]


        def _on_ok() -> None:
            code = code_var.get().strip().upper()
            if not _TYPE_CODE_RE.match(code):
                messagebox.showerror(
                    "Document Rule", f"Invalid document code: {code}", parent=editor
                )
                return
            if is_new and code in self.working:
                messagebox.showerror(
                    "Document Rule", f"{code} already exists", parent=editor
                )
                return
            try:
                priority = int(str(priority_var.get()).strip() or "50")
            except ValueError:
                messagebox.showerror(
                    "Document Rule", "Priority must be a number.", parent=editor
                )
                return
            try:
                type_min_hits = max(1, int(str(min_hits_var.get()).strip() or "1"))
            except ValueError:
                messagebox.showerror(
                    "Document Rule", "Min hits must be a number.", parent=editor
                )
                return
            raw_advanced = advanced_text.get("1.0", "end").strip() or "{}"
            try:
                advanced_values = json.loads(raw_advanced)
            except json.JSONDecodeError as exc:
                messagebox.showerror(
                    "Document Rule", f"Advanced JSON is invalid: {exc}", parent=editor
                )
                return
            if not isinstance(advanced_values, dict):
                messagebox.showerror(
                    "Document Rule",
                    "Advanced JSON must be an object { ... }.",
                    parent=editor,
                )
                return
            managed_clash = sorted(
                key for key in advanced_values if key in _MANAGED_RULE_KEYS
            )
            if managed_clash:
                messagebox.showerror(
                    "Document Rule",
                    "Advanced JSON must not contain managed keys: "
                    + ", ".join(managed_clash),
                    parent=editor,
                )
                return

            page_rules: Dict[str, Dict[str, Any]] = {}
            for entry in page_rows:
                if not entry["frame"].winfo_exists():
                    continue
                key = entry["key"].get().strip().lower()
                if not key:
                    continue
                if key != "single" and not key.isdigit():
                    messagebox.showerror(
                        "Document Rule",
                        f'Invalid page key "{key}" (use a page number or "single").',
                        parent=editor,
                    )
                    return
                try:
                    min_hits = max(
                        1, int(str(entry["min_hits"].get()).strip() or "1")
                    )
                except ValueError:
                    messagebox.showerror(
                        "Document Rule",
                        f"min_hits for page {key} must be a number.",
                        parent=editor,
                    )
                    return
                page_spec: Dict[str, Any] = dict(entry["advanced"])
                page_spec["enabled"] = bool(entry["enabled"].get())
                page_spec["keywords"] = [
                    kw.strip()
                    for kw in entry["keywords"].get().split(",")
                    if kw.strip()
                ]
                if min_hits != 1:
                    page_spec["min_hits"] = min_hits
                strong = [
                    kw.strip()
                    for kw in entry["strong"].get().split(",")
                    if kw.strip()
                ]
                if strong:
                    page_spec["strong"] = strong
                page_rules[key] = page_spec

            if "single" in page_rules and code != "SOA2":
                proceed = messagebox.askyesno(
                    "Document Rule",
                    f'Rule {code} defines a "single" page rule, but only SOA2 '
                    "has a downstream handler for plain (complete-form) output.\n\n"
                    "Other multi-page types should only define page 1 / page 2 "
                    "rules, otherwise a detected complete form has no merge "
                    "handler.\n\nContinue anyway?",
                    parent=editor,
                )
                if not proceed:
                    return

            updated: Dict[str, Any] = dict(advanced_values)
            updated["enabled"] = bool(enabled_var.get())
            updated["priority"] = priority
            if type_min_hits != 1:
                updated["min_hits"] = type_min_hits
            updated["keywords"] = _text_lines(keywords_text)
            strong_lines = _text_lines(strong_text)
            if strong_lines:
                updated["strong"] = strong_lines
            updated["aliases"] = _text_lines(aliases_text)
            if page_rules:
                updated["page_rules"] = page_rules
            if not is_new and code != doc_type:
                self.working.pop(doc_type, None)
            self.working[code] = updated
            self._refresh_table()
            self._log(
                f"Document detection rule {'added' if is_new else 'updated'}: "
                f"{code} (not saved yet)"
            )
            editor.destroy()

        ttk.Button(
            footer, text="OK", command=_on_ok, style="Accent.TButton"
        ).pack(side="right")
        ttk.Button(footer, text="Cancel", command=editor.destroy).pack(
            side="right", padx=(0, 8)
        )
        editor.grab_set()


if __name__ == "__main__":
    def _find_widgets(widget, cls):
        found = [
            child for child in widget.winfo_children() if isinstance(child, cls)
        ]
        for child in widget.winfo_children():
            found.extend(_find_widgets(child, cls))
        return found

    def _open_editor_for(dialog, doc_type):
        before = set(dialog.dialog.winfo_children())
        dialog._open_editor(doc_type, is_new=False)
        dialog.dialog.update_idletasks()
        editors = [
            w
            for w in dialog.dialog.winfo_children()
            if isinstance(w, tk.Toplevel) and w not in before
        ]
        assert editors, f"type editor must open for {doc_type}"
        return editors[0]

    def _click_ok(editor):
        ok_buttons = [
            b
            for b in _find_widgets(editor, ttk.Button)
            if b.cget("text") == "OK"
        ]
        assert ok_buttons, "OK button must exist"
        ok_buttons[0].invoke()

    root = tk.Tk()
    root.withdraw()
    messages: List[str] = []
    dialog = DocumentDetectionRulesDialog(root, log_callback=messages.append)
    dialog.dialog.update_idletasks()
    assert dialog.working, "working rules must not be empty"
    assert "CSF" in dialog.working, "default rules must include CSF"
    print(f"[PASS] dialog opened with {len(dialog.working)} document types")

    rows = {
        dialog.tree.item(item, "values")[0]: dialog.tree.item(item, "values")
        for item in dialog.tree.get_children()
    }
    assert rows["SOA2"][3] == "1, 2, single", f"SOA2 pages column: {rows['SOA2']}"
    assert rows["CSF"][3] == "single-page", f"CSF pages column: {rows['CSF']}"
    print("[PASS] Pages column: SOA2 = '1, 2, single', CSF = 'single-page'")

    # --- type-level min_hits editing (ANR default = 2) ---
    editor = _open_editor_for(dialog, "ANR")
    spinboxes = _find_widgets(editor, ttk.Spinbox)
    assert len(spinboxes) >= 2, "priority and min_hits spinboxes must exist"
    assert spinboxes[1].get() == "2", f"ANR min_hits default: {spinboxes[1].get()}"
    spinboxes[1].delete(0, "end")
    spinboxes[1].insert(0, "3")
    _click_ok(editor)
    assert dialog.working["ANR"]["min_hits"] == 3, "ANR min_hits write-back"
    print("[PASS] type-level min_hits edited and written back (ANR = 3)")

    # --- strong keywords editing (CSF has none by default) ---
    editor = _open_editor_for(dialog, "CSF")
    texts = _find_widgets(editor, tk.Text)
    assert len(texts) >= 4, "keywords/strong/aliases/advanced boxes must exist"
    assert not texts[1].get("1.0", "end").strip(), "CSF strong must be empty"
    texts[1].insert("1.0", "claim signature form\nphilhealth csf")
    _click_ok(editor)
    assert dialog.working["CSF"]["strong"] == [
        "claim signature form",
        "philhealth csf",
    ], "CSF strong write-back"
    print("[PASS] strong keywords edited and written back for CSF")

    # --- advanced JSON preserved and editable (DTR suppress_if) ---
    editor = _open_editor_for(dialog, "DTR")
    texts = _find_widgets(editor, tk.Text)
    advanced_raw = texts[3].get("1.0", "end").strip()
    assert "suppress_if" in advanced_raw, "DTR advanced JSON shows suppress_if"
    texts[3].delete("1.0", "end")
    texts[3].insert(
        "1.0", '{"suppress_if": ["please pay at the cashier"], "custom_key": 7}'
    )
    _click_ok(editor)
    assert dialog.working["DTR"]["suppress_if"] == ["please pay at the cashier"]
    assert dialog.working["DTR"]["custom_key"] == 7, "custom advanced key write-back"
    print("[PASS] advanced JSON edited (suppress_if + custom key) for DTR")

    # --- page-level strong editing, other page fields preserved (SOA2) ---
    editor = _open_editor_for(dialog, "SOA2")
    strong_entries = [
        e
        for e in _find_widgets(editor, ttk.Entry)
        if int(e.cget("width")) == 20
    ]
    assert len(strong_entries) == 3, f"SOA2 strong entries: {len(strong_entries)}"
    assert "statement of account" in strong_entries[0].get(), (
        f"SOA2 page 1 strong entry: {strong_entries[0].get()!r}"
    )
    strong_entries[0].delete(0, "end")
    strong_entries[0].insert(0, "test strong phrase")
    _click_ok(editor)
    soa2_page1 = dialog.working["SOA2"]["page_rules"]["1"]
    assert soa2_page1["strong"] == ["test strong phrase"], "page strong write-back"
    assert soa2_page1["min_hits"] == 5, "page min_hits preserved"
    assert "fuzzy" in dialog.working["SOA2"]["page_rules"]["2"], "page fuzzy preserved"
    print("[PASS] page-level strong edited; min_hits/fuzzy preserved for SOA2")

    dialog.dialog.destroy()
    root.destroy()
    print("RESULT: PASSED")
