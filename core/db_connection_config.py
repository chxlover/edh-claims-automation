"""Server & Database Credentials Configuration (HBSys MySQL) — v1.

Local, git-ignored configuration for the HBSys MySQL server connection
used by the read-only repositories (`fees_checker`, `claims_checker`
helpers, `not_transmitted_batches`, `patient_review_queue`, resume
manager, date-fill verifier).

Design (task spec):
    Configuration GUI (gui/db_connection_dialog.py)
            ↓
    this module (Configuration Manager)
            ↓
    core.hbsys_connection.create_hbsys_connection (existing factory —
    UNTOUCHED; credentials reach it through the SAME HBSYS_DB_* env
    vars it already reads)
            ↓
    MySQL server

Rules honored:
    * NO business/connection logic duplicated — `test_connection`
      uses the exact same pymysql parameters as the existing factory,
      including charset="utf8" (NOT pymysql's utf8mb4 default — old
      HBSys MySQL servers reject utf8mb4 with "unknown character set").
    * NO hardcoded credentials here; defaults mirror the existing
      factory defaults only (they are already public in the codebase).
    * The password is NEVER included in error messages, logs, or
      exceptions (scrubbed deterministically in `test_connection`).
    * Persistence: `db_connection_config.json` in the project root —
      same local-settings policy as `claims_gui_config.json`
      (git-ignored; never committed).
    * Existing connections are preserved: with no saved config the
      module changes nothing — the factory keeps using its env/defaults
      exactly as before.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "db_connection_config.json"

# JSON field names (task spec: DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD)
FIELD_HOST = "DB_HOST"
FIELD_PORT = "DB_PORT"
FIELD_NAME = "DB_NAME"
FIELD_USER = "DB_USER"
FIELD_PASSWORD = "DB_PASSWORD"

# The env vars the EXISTING factory (core/hbsys_connection.py) reads.
ENV_MAP = {
    FIELD_HOST: "HBSYS_DB_HOST",
    FIELD_PORT: "HBSYS_DB_PORT",
    FIELD_NAME: "HBSYS_DB_NAME",
    FIELD_USER: "HBSYS_DB_USER",
    FIELD_PASSWORD: "HBSYS_DB_PASSWORD",
}

# Same fallback values the existing factory already uses — no new secrets.
DEFAULTS = {
    FIELD_HOST: "192.168.1.2",
    FIELD_PORT: 3306,
    FIELD_NAME: "hbsys_edh",
    FIELD_USER: "root",
    FIELD_PASSWORD: "",
}

PORT_MIN = 1
PORT_MAX = 65535


def load_connection_config(path: Optional[Path] = CONFIG_FILE) -> dict:
    """Load the saved credentials; missing file/corrupt JSON -> defaults.

    Never raises: a missing or unreadable config simply falls back to
    the existing factory defaults (current behavior preserved).
    """
    config = dict(DEFAULTS)
    path = Path(path)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return config
        if isinstance(raw, dict):
            for field in (FIELD_HOST, FIELD_NAME, FIELD_USER, FIELD_PASSWORD):
                if raw.get(field) is not None:
                    config[field] = str(raw[field])
            if raw.get(FIELD_PORT) is not None:
                config[FIELD_PORT] = raw[FIELD_PORT]
    return config


def validate_connection_config(config: dict) -> list[str]:
    """User-friendly validation errors (empty list = valid).

    Checks: host required; port numeric within 1-65535; database name
    required; username required. (Password may legitimately be empty —
    MySQL allows it; the existing default setup uses a password but
    validation does not force one.)
    """
    errors: list[str] = []

    host = str(config.get(FIELD_HOST) or "").strip()
    if not host:
        errors.append("Server / Host is required.")

    port_value = str(config.get(FIELD_PORT) or "").strip()
    try:
        port = int(port_value)
    except ValueError:
        errors.append("Port must be a number.")
    else:
        if not (PORT_MIN <= port <= PORT_MAX):
            errors.append(f"Port must be between {PORT_MIN} and {PORT_MAX}.")

    if not str(config.get(FIELD_NAME) or "").strip():
        errors.append("Database name is required.")

    if not str(config.get(FIELD_USER) or "").strip():
        errors.append("Username is required.")

    return errors


def save_connection_config(config: dict, path: Optional[Path] = CONFIG_FILE) -> Path:
    """Validate + persist the credentials to the local JSON file."""
    errors = validate_connection_config(config)
    if errors:
        raise ValueError("; ".join(errors))
    payload = {
        FIELD_HOST: str(config[FIELD_HOST]).strip(),
        FIELD_PORT: int(str(config[FIELD_PORT]).strip()),
        FIELD_NAME: str(config[FIELD_NAME]).strip(),
        FIELD_USER: str(config[FIELD_USER]).strip(),
        FIELD_PASSWORD: str(config.get(FIELD_PASSWORD) or ""),
    }
    path = Path(path)
    path.write_text(
        json.dumps(payload, indent=4, ensure_ascii=False), encoding="utf-8"
    )
    return path


def apply_to_env(config: dict) -> None:
    """Export the credentials to the HBSYS_DB_* env vars.

    This is the ONLY integration point: the existing factory and every
    subprocess launched by the GUI (env = os.environ.copy()) keep
    reading the same variables — nothing else changes.
    """
    for field, env_name in ENV_MAP.items():
        value = config.get(field)
        if value is not None:
            os.environ[env_name] = str(value)


def _scrub_secret(message: str, *secrets: str) -> str:
    """Remove secret values from an error message (never leaked)."""
    cleaned = str(message)
    for secret in secrets:
        secret = str(secret or "")
        if secret:
            cleaned = cleaned.replace(secret, "•••••")
    return cleaned


def test_connection(config: dict) -> tuple[bool, str]:
    """Try connecting with the given values; ALWAYS closes the handle.

    Returns (ok, message). The password never appears in the message.
    The message includes a short, scrubbed technical reason plus the
    task's checklist so a non-programmer can act on it.
    """
    errors = validate_connection_config(config)
    if errors:
        return False, "Invalid configuration:\n" + "\n".join(
            f"• {error}" for error in errors
        )

    host = str(config[FIELD_HOST]).strip()
    port = int(str(config[FIELD_PORT]).strip())
    database = str(config[FIELD_NAME]).strip()
    user = str(config[FIELD_USER]).strip()
    password = str(config.get(FIELD_PASSWORD) or "")

    try:
        import pymysql
    except ImportError:
        return False, "✗ Connection failed\n\npymysql is not installed."

    connection = None
    try:
        # Identical parameters to the existing factory (short timeout);
        # charset="utf8" — NOT utf8mb4 — exactly like
        # core.hbsys_connection.create_hbsys_connection.
        connection = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=5,
            charset="utf8",
        )
    except Exception as exc:
        reason = _scrub_secret(f"{type(exc).__name__}: {exc}", password)
        return (
            False,
            "✗ Connection failed\n\n"
            "Unable to connect to the database.\n\n"
            f"Reason: {reason}\n\n"
            "Check:\n"
            "• Server / Host\n"
            "• Port\n"
            "• Username\n"
            "• Password\n"
            "• Database name\n"
            "• Network connection",
        )
    finally:
        if connection is not None:
            try:
                connection.close()  # always closed after testing
            except Exception:
                pass

    return (
        True,
        f"✓ Connection successful\nServer: {host}\nDatabase: {database}",
    )


# -- standalone test (AGENTS.md: every module must support __main__) -------

if __name__ == "__main__":
    import tempfile

    failures = 0

    def check(label: str, ok: bool) -> None:
        global failures
        if not ok:
            failures += 1
        print(f"{'OK  ' if ok else 'FAIL'} {label}")

    # load: missing file -> existing factory defaults
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "missing.json"
        config = load_connection_config(empty)
        check(
            "load: missing file -> factory defaults (no behavior change)",
            config == DEFAULTS,
        )

        # corrupt file -> defaults, never an exception
        bad = Path(tmp) / "bad.json"
        bad.write_text("{ not json", encoding="utf-8")
        check(
            "load: corrupt JSON -> defaults (graceful)",
            load_connection_config(bad) == DEFAULTS,
        )

        # validation errors — user-friendly, all fields covered
        check(
            "validate: every required-field error reported",
            validate_connection_config(
                {FIELD_HOST: "", FIELD_PORT: "abc", FIELD_NAME: "", FIELD_USER: ""}
            )
            == [
                "Server / Host is required.",
                "Port must be a number.",
                "Database name is required.",
                "Username is required.",
            ],
        )
        check(
            "validate: port range enforced",
            "Port must be between 1 and 65535."
            in validate_connection_config(
                {FIELD_HOST: "h", FIELD_PORT: "99999", FIELD_NAME: "d", FIELD_USER: "u"}
            ),
        )
        check(
            "validate: minimal valid config passes (empty password allowed)",
            validate_connection_config(
                {FIELD_HOST: "h", FIELD_PORT: "3306", FIELD_NAME: "d", FIELD_USER: "u"}
            )
            == [],
        )

        # save refuses invalid config
        try:
            save_connection_config({FIELD_HOST: "", FIELD_PORT: "3306",
                                     FIELD_NAME: "d", FIELD_USER: "u"}, bad)
            check("save: invalid config refused", False)
        except ValueError:
            check("save: invalid config refused", True)

        # save/load round-trip (password stays out of source code, in local JSON)
        good = {
            FIELD_HOST: "127.0.0.1", FIELD_PORT: "3307", FIELD_NAME: "claims_db",
            FIELD_USER: "operator", FIELD_PASSWORD: "SECRETPW123",
        }
        path = save_connection_config(good, bad)
        loaded = load_connection_config(bad)
        check(
            "save/load: round-trip identical (port normalized to int)",
            loaded[FIELD_HOST] == "127.0.0.1"
            and loaded[FIELD_PORT] == 3307
            and loaded[FIELD_NAME] == "claims_db"
            and loaded[FIELD_USER] == "operator"
            and loaded[FIELD_PASSWORD] == "SECRETPW123"
            and path == bad,
        )
        # the file is plain local JSON (same policy as claims_gui_config.json)
        raw = json.loads(bad.read_text(encoding="utf-8"))
        check(
            "save: file schema uses the spec field names",
            set(raw) == {"DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"},
        )

        # apply_to_env feeds the EXISTING factory variables
        env_test = {FIELD_HOST: "10.0.0.5", FIELD_PORT: 3307, FIELD_NAME: "dbx",
                    FIELD_USER: "ux", FIELD_PASSWORD: "px"}
        for name in ENV_MAP.values():
            os.environ.pop(name, None)
        apply_to_env(env_test)
        check(
            "env: HBSYS_DB_* variables set for the existing factory",
            os.environ["HBSYS_DB_HOST"] == "10.0.0.5"
            and os.environ["HBSYS_DB_PORT"] == "3307"
            and os.environ["HBSYS_DB_NAME"] == "dbx"
            and os.environ["HBSYS_DB_USER"] == "ux"
            and os.environ["HBSYS_DB_PASSWORD"] == "px",
        )

        # test_connection: invalid config -> graceful validation failure
        ok, message = test_connection({FIELD_HOST: "", FIELD_PORT: "x",
                                       FIELD_NAME: "", FIELD_USER: ""})
        check(
            "test: invalid config -> False + validation message",
            ok is False and "Server / Host is required" in message,
        )

        # test_connection: refused endpoint -> graceful failure, NO password leak
        ok, message = test_connection(
            {FIELD_HOST: "127.0.0.1", FIELD_PORT: 1, FIELD_NAME: "no_db",
             FIELD_USER: "nobody", FIELD_PASSWORD: "SECRETPW123"}
        )
        check(
            "test: refused connection -> False + checklist message",
            ok is False and "✗ Connection failed" in message
            and "• Network connection" in message,
        )
        check(
            "test: password NEVER leaked in failure message",
            "SECRETPW123" not in message,
        )

        # test_connection must use charset="utf8" — the SAME value as the
        # existing factory (core.hbsys_connection), NOT pymysql's utf8mb4
        # default (owner fix 2026-09-11: "unknown character set utf8mb4").
        import core.db_connection_config as dbcc
        import pymysql as _pymysql

        captured_kwargs: dict = {}

        def _capture_connect(*args, **kwargs):
            captured_kwargs.update(kwargs)
            raise RuntimeError("capture-only")

        original_connect = _pymysql.connect
        _pymysql.connect = _capture_connect
        try:
            dbcc.test_connection(
                {FIELD_HOST: "h", FIELD_PORT: "3306", FIELD_NAME: "d",
                 FIELD_USER: "u", FIELD_PASSWORD: "p"}
            )
        finally:
            _pymysql.connect = original_connect
        check(
            "test: charset='utf8' exactly like the existing factory",
            captured_kwargs.get("charset") == "utf8"
            and "utf8mb4" not in str(captured_kwargs.get("charset")),
        )

        # scrub: secret replaced everywhere it occurs
        check(
            "scrub: secret value masked",
            _scrub_secret("auth SECRETPW123 failed for SECRETPW123", "SECRETPW123")
            == "auth ••••• failed for •••••",
        )

    print("RESULT:", "PASSED" if failures == 0 else f"{failures} FAILURE(S)")
    raise SystemExit(0 if failures == 0 else 1)
