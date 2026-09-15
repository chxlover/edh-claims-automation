"""Server & Database Credentials Configuration dialog (HBSys MySQL).

A small Tkinter Toplevel opened from the Preferences tab. Follows the
existing GUI patterns: ttk widgets, `messagebox` feedback, no external
dependencies, and the tab's muted/professional look.

Flow (task spec):

    Configuration GUI (this module)
            ↓
    Configuration Manager (core/db_connection_config.py)
            ↓
    Database Connection (core/hbsys_connection.py — existing factory,
    reached through the same HBSYS_DB_* env vars)
            ↓
    MySQL server

Behavior:
    * OPEN      — loads the previously saved settings into the fields;
                  the password stays masked until Show/Hide is toggled.
    * TEST      — validates, connects with the ENTERED values (never
                  saves them), always closes the connection, shows a
                  clear success/failure message; the password never
                  appears in any message or log.
    * SAVE      — validates, persists to db_connection_config.json and
                  applies to the HBSYS_DB_* environment so the EXISTING
                  factory (and any script the GUI later launches) picks
                  it up. Existing functionality untouched — with no
                  save, nothing changes.
    * CLOSE     — discards nothing that was not explicitly saved.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Optional

from core.db_connection_config import (
    FIELD_HOST,
    FIELD_NAME,
    FIELD_PASSWORD,
    FIELD_PORT,
    FIELD_USER,
    load_connection_config,
    save_connection_config,
    test_connection,
    validate_connection_config,
)


class DBConnectionDialog:
    """Modal credentials editor (one instance at a time)."""

    def __init__(
        self,
        parent: tk.Misc,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.log_callback = log_callback or (lambda _line: None)
        saved = load_connection_config()

        self.window = tk.Toplevel(parent)
        self.window.title("Server & Database Configuration")
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.grab_set()  # modal

        body = ttk.Frame(self.window, padding=16)
        body.pack(fill="both", expand=True)

        # -- Connection Settings -------------------------------------------
        form = ttk.LabelFrame(body, text="Connection Settings", padding=12)
        form.pack(fill="x")

        self.var_host = tk.StringVar(value=str(saved[FIELD_HOST]))
        self.var_port = tk.StringVar(value=str(saved[FIELD_PORT]))
        self.var_name = tk.StringVar(value=str(saved[FIELD_NAME]))
        self.var_user = tk.StringVar(value=str(saved[FIELD_USER]))
        self.var_password = tk.StringVar(value=str(saved[FIELD_PASSWORD]))
        self.show_password = tk.BooleanVar(value=False)

        rows = [
            ("Server / Host", self.var_host, "e.g. 192.168.1.2"),
            ("Port", self.var_port, "e.g. 3306"),
            ("Database Name", self.var_name, "e.g. hbsys_edh"),
            ("Username", self.var_user, "e.g. root"),
        ]
        for row, (label, variable, hint) in enumerate(rows):
            ttk.Label(form, text=label, width=16).grid(
                row=row, column=0, sticky="w", pady=4
            )
            entry = ttk.Entry(form, textvariable=variable, width=34)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            ttk.Label(form, text=hint, foreground="#64748B").grid(
                row=row, column=2, sticky="w", padx=(8, 0)
            )

        # Password row with Show/Hide toggle (masked by default)
        password_row = len(rows)
        ttk.Label(form, text="Password", width=16).grid(
            row=password_row, column=0, sticky="w", pady=4
        )
        self.password_entry = ttk.Entry(
            form, textvariable=self.var_password, width=34, show="•"
        )
        self.password_entry.grid(
            row=password_row, column=1, sticky="ew", pady=4
        )
        self.show_hide_button = ttk.Button(
            form, text="Show", width=8, command=self.toggle_password
        )
        self.show_hide_button.grid(
            row=password_row, column=2, sticky="w", padx=(8, 0)
        )

        form.columnconfigure(1, weight=1)

        # -- Actions ---------------------------------------------------------
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(
            actions, text="Test Connection", command=self.on_test
        ).pack(side="left")
        ttk.Button(
            actions, text="Save Configuration", command=self.on_save
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            actions, text="Close", command=self.window.destroy
        ).pack(side="right")

        # -- Status ------------------------------------------------------------
        self.status_var = tk.StringVar(value="Loaded saved configuration.")
        ttk.Label(body, textvariable=self.status_var, foreground="#64748B").pack(
            anchor="w", pady=(12, 0)
        )

        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        self.window.update_idletasks()
        center_on_parent(self.window, parent)

    # -- helpers -------------------------------------------------------------

    def _collect(self) -> dict:
        """Current field values as a config dict (nothing saved)."""
        return {
            FIELD_HOST: self.var_host.get(),
            FIELD_PORT: self.var_port.get(),
            FIELD_NAME: self.var_name.get(),
            FIELD_USER: self.var_user.get(),
            FIELD_PASSWORD: self.var_password.get(),
        }

    def toggle_password(self) -> None:
        """Show/Hide the password (masked by default)."""
        if self.show_password.get():
            # currently shown -> mask again
            self.password_entry.configure(show="•")
            self.show_hide_button.configure(text="Show")
            self.show_password.set(False)
        else:
            # currently masked -> reveal
            self.password_entry.configure(show="")
            self.show_hide_button.configure(text="Hide")
            self.show_password.set(True)

    # -- actions ---------------------------------------------------------------

    def on_test(self) -> None:
        """Test with the ENTERED values; never save; always close."""
        errors = validate_connection_config(self._collect())
        if errors:
            messagebox.showwarning(
                "Connection Settings", "\n".join(errors), parent=self.window
            )
            return

        self.status_var.set("Testing connection…")
        self.window.update_idletasks()

        # Only the core module handles the connection; the password is
        # scrubbed there and never reaches this GUI's logs.
        ok, message = test_connection(self._collect())
        if ok:
            self.status_var.set("✓ Connection successful")
            messagebox.showinfo("Test Connection", message, parent=self.window)
            self.log_callback("[DB] Test Connection: successful")
        else:
            self.status_var.set("✗ Connection failed")
            messagebox.showerror("Test Connection", message, parent=self.window)
            # no host/user/password values are ever logged
            self.log_callback("[DB] Test Connection: failed")

    def on_save(self) -> None:
        """Validate, persist, and apply to the HBSYS_DB_* environment."""
        config = self._collect()
        errors = validate_connection_config(config)
        if errors:
            messagebox.showwarning(
                "Connection Settings", "\n".join(errors), parent=self.window
            )
            return
        try:
            path = save_connection_config(config)
        except (ValueError, OSError) as exc:
            messagebox.showerror(
                "Save Configuration", f"Could not save:\n{exc}", parent=self.window
            )
            return

        # Feed the EXISTING factory through its own env variables.
        from core.db_connection_config import apply_to_env

        apply_to_env(config)
        self.status_var.set("✓ Configuration saved")
        self.log_callback(
            f"[DB] Server & database configuration saved ({path.name})"
        )
        messagebox.showinfo(
            "Save Configuration",
            "Configuration saved.\n\n"
            "New connections will use these settings. "
            "Scripts launched from now on inherit them automatically.",
            parent=self.window,
        )


def center_on_parent(window: tk.Toplevel, parent: tk.Misc) -> None:
    """Center the dialog over its parent window."""
    try:
        window.update_idletasks()
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()
        width = window.winfo_reqwidth()
        height = window.winfo_reqheight()
        x = parent_x + (parent_w - width) // 2
        y = parent_y + (parent_h - height) // 2
        window.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    except Exception:
        pass


def open_db_connection_dialog(
    parent: tk.Misc, log_callback: Optional[Callable[[str], None]] = None
) -> DBConnectionDialog:
    """Create (and return) the credentials dialog — used by the shell."""
    return DBConnectionDialog(parent, log_callback=log_callback)


# -- standalone test (AGENTS.md: every module must support __main__) -------

if __name__ == "__main__":
    # Headless-safe test: with a display, builds the dialog and drives
    # validation/masking end-to-end WITHOUT saving or connecting; the
    # save path is tested against a temp config file via the core
    # module's own tests. Without a display, import-level checks run.
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError as exc:
        print(f"SKIP display unavailable ({exc}) — import checks only")
        from core.db_connection_config import DEFAULTS, validate_connection_config

        assert DEFAULTS[FIELD_HOST] == "192.168.1.2"
        assert validate_connection_config(DEFAULTS) == []
        assert open_db_connection_dialog is not None
        print("OK   dialog: import + defaults verified headless")
        print("RESULT: PASSED")
        raise SystemExit(0)

    logs: list[str] = []
    dialog = DBConnectionDialog(root, log_callback=logs.append)
    dialog.window.update_idletasks()

    # suppress messageboxes for headless drive (never block the test)
    shown: list[tuple[str, str]] = []
    from tkinter import messagebox as _mb

    _mb.showwarning = lambda title, msg, **_k: shown.append((title, msg))
    _mb.showinfo = lambda title, msg, **_k: shown.append((title, msg))
    _mb.showerror = lambda title, msg, **_k: shown.append((title, msg))

    # fields populated from saved config; password masked (bullet char may
    # come back mojibake from cget — compare via the show state instead)
    assert dialog.var_host.get(), "host loaded"
    assert dialog.var_port.get() and dialog.var_port.get().isdigit(), "port loaded"
    assert dialog.var_name.get(), "database loaded"
    assert dialog.var_user.get(), "username loaded"
    assert dialog.show_password.get() is False, "password masked by default"

    # Show/Hide toggle both directions
    dialog.toggle_password()
    assert dialog.show_password.get() is True, "password shown"
    assert dialog.show_hide_button.cget("text") == "Hide"
    dialog.toggle_password()
    assert dialog.show_password.get() is False, "password masked again"
    assert dialog.show_hide_button.cget("text") == "Show"

    # validation surfaces user-friendly errors (no crash, no blocking)
    dialog.var_host.set("")
    dialog.var_port.set("abc")
    dialog.on_test()
    assert shown, "validation warning displayed"
    assert "Server / Host is required" in shown[-1][1]
    assert "Port must be a number" in shown[-1][1]

    # test with a dead endpoint: graceful failure, checklist message
    dialog.var_host.set("127.0.0.1")
    dialog.var_port.set("3306")
    dialog.var_name.set("no_such_database_for_test")
    dialog.var_user.set("nobody")
    dialog.var_password.set("SECRETPW123")
    shown.clear()
    dialog.on_test()
    assert dialog.status_var.get().startswith("✗"), "status shows failure"
    assert any("✗ Connection failed" in msg for _t, msg in shown)
    assert any("• Network connection" in msg for _t, msg in shown)

    # no password value ever reaches the logs or the shown messages
    assert not any("SECRETPW123" in line for line in logs)
    assert not any("SECRETPW123" in msg for _t, msg in shown)

    dialog.window.destroy()
    root.destroy()
    print("OK   dialog: construction, load, masking, validation, test, logs")
    print("RESULT: PASSED")
