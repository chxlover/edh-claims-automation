"""Configurable document detection rules (optional detection mode).

Implements the \"Document Detection Rules Configurable\" feature:

    OFF (default)  ->  the EXISTING hardcoded detector inside
                       bot_unknown_trainer_DEFERRED_OUTPUT_REVIEW_PATSUFFIX_ADM_DIS.py
                       runs and this module has NO effect.
    ON             ->  detect_doc_configurable() is the ONLY detector executed;
                       the hardcoded detectors are bypassed (results are never
                       merged) and the returned document type feeds the SAME
                       existing downstream business logic.

Storage:
    document_detection_rules.json (project root) - local configuration with the
    same policy as claims_gui_config.json / doctors_config.json.  The default
    rules live in DEFAULT_RULES below (Restore Defaults writes them to the
    file).  They are a configurable representation of the hardcoded keyword
    tables of the production engine - the engine code itself is NOT changed by
    them and stays the untouched fallback for OFF mode.

Result contract (identical to the hardcoded detector):
    <TYPE>, <TYPE>_page1, <TYPE>_page2, ..., or \"UNKNOWN\".  Page rules are
    evaluated in their configured order; when two or more numeric page rules
    match and a \"single\" rule exists, the plain type is returned (complete
    form, e.g. SOA2).  Priority resolves competing rules; an exact priority
    tie is AMBIGUOUS and returns \"UNKNOWN\" so the EXISTING review flow
    handles it (owner decision 2026-09-16, option A).

Known, documented simplifications of DEFAULT_RULES versus the hardcoded
path (the hardcoded path itself is untouched):
    * weighted scoring of core/soa2_resolver.py becomes keyword hit counts
    * compound conditions (MRF \"UHC + page1 context\", COE benefit/portal
      identities) are not expressible and are omitted from the defaults
    * CF2 page order differs from the hardcoded guard order only in the rare
      case where both pages reach their hit thresholds without unique fields
Deterministic keyword matching only - no AI/ML classification.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - matching stays exact-only without it
    fuzz = None

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE_DIR = Path(__file__).resolve().parent.parent
RULES_FILE = BASE_DIR / "document_detection_rules.json"
CONFIG_FILE = BASE_DIR / "claims_gui_config.json"
CONFIG_KEY = "document_detection_rules_configurable"
ENV_VAR = "CLAIMS_DOC_DETECTION_RULES_CONFIGURABLE"
SCHEMA_VERSION = 1

MAX_KEYWORDS_PER_LIST = 300
MAX_KEYWORD_LENGTH = 200

_TYPE_RE = re.compile(r"^[A-Za-z0-9_]{2,24}$")
_PAGE_RE = re.compile(r"^\d{1,3}$")

_LAST_DIAGNOSTICS: Dict[str, Any] = {}
_RULES_CACHE: Dict[str, Any] = {"mtime": None, "types": None, "error": ""}


def is_configurable_enabled() -> bool:
    """Toggle check for GUI/tests (the engine has its own equivalent helper).

    Priority: 1) CLAIMS_DOC_DETECTION_RULES_CONFIGURABLE env var from the GUI
    subprocess, 2) claims_gui_config.json -> document_detection_rules_configurable,
    3) False (hardcoded detection stays the default).
    """
    value = os.environ.get(ENV_VAR)
    if value is not None:
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    try:
        if CONFIG_FILE.is_file():
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if CONFIG_KEY in config:
                return bool(config.get(CONFIG_KEY))
    except (OSError, ValueError):
        pass
    return False


def normalize_text(text: str) -> str:
    """Mirror of the engine's normalize_text (kept local on purpose - the
    5,000-line engine script must not be imported by a config module)."""
    value = str(text or "").upper()
    value = value.replace("\u00d1", "N")
    value = re.sub(r"[^A-Z0-9\- ]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value

# ---------------------------------------------------------------
# DEFAULT RULES - a configurable representation of the hardcoded
# keyword tables in bot_unknown_trainer_DEFERRED_OUTPUT_REVIEW_
# PATSUFFIX_ADM_DIS.py (engine lines 129-175, 1231-1345, 2153-2915)
# and core/soa2_resolver.py.  Priorities encode the hardcoded match
# order: MRF -> PBC -> OPR -> ANR -> CF2 -> CSF -> COE -> DTR guard
# -> SOA -> remaining DOC_KEYWORDS -> UNKNOWN.  The hardcoded code
# itself is NOT touched by these defaults.
# ---------------------------------------------------------------
DEFAULT_RULES: Dict[str, Dict[str, Any]] = {
    "MRF": {
        "enabled": True,
        "priority": 100,
        "keywords": [],
        "aliases": [],
        # CF2 guard (mirrors detect_mrf_type): CF2 page 1 can contain generic
        # PhilHealth/MRF-looking labels, so a CF2 identity blocks MRF.
        "suppress_if": ["claim form 2", "cf2"],
        "page_rules": {
            "1": {
                "enabled": True,
                "strong": ["philhealth member registration form", "member registration form", "philsys id number", "philsys"],
                # Context keywords mirror detect_mrf_type's has_uhc + context
                # rule: "uhc" must be present plus at least one page-1 context
                # marker (min_hits=2).  Without "uhc" in the list and with
                # min_hits=1, any CSF page (which always contains
                # "philhealth identification number") was misdetected as
                # MRF_page1 (2026-09-23 CSF bug).
                # NOTE: the hardcoded "purpose:" marker is intentionally NOT
                # here - _hit() also matches the normalized form, so
                # "purpose:" degraded to the plain word "purpose", which
                # appears in CSF Part I text and gave the MRF rule a false
                # second hit.
                "keywords": ["uhc", "personal details", "philhealth identification number", "pin is your unique", "registration", "preferred konsulta provider", "maiden name"],
                "min_hits": 2,
                "fuzzy": {"threshold": 84, "min_hits": 1, "per_line": False, "keywords": ["PHILHEALTH MEMBER REGISTRATION FORM", "MEMBER REGISTRATION FORM", "PHILSYS ID NUMBER", "PHILSYS"]},
            },
            "2": {
                "enabled": True,
                "strong": ["for philhealth use only", "for philhealth use"],
                "keywords": ["updating/amendment", "updating amendment", "v. updating/amendment", "v updating amendment", "change/correction of name", "correction of date of birth", "correction of sex", "change of civil status", "updating of personal information", "under penalty of law", "documents i have attached", "full name:"],
                "min_hits": 2,
                "fuzzy": {"threshold": 82, "min_hits": 1, "per_line": False, "keywords": ["CHANGE CORRECTION OF NAME", "CORRECTION OF DATE OF BIRTH", "CHANGE OF CIVIL STATUS", "UPDATING OF PERSONAL INFORMATION", "UNDER PENALTY OF LAW"]},
            },
        },
    },
    "PBC": {
        "enabled": True,
        "priority": 95,
        "keywords": [],
        "aliases": [],
        "suppress_if": [],
        "page_rules": {
            "1": {
                "enabled": True,
                "strong": [],
                "keywords": ["certificate of live birth", "office of the civil registrar general", "municipal form no. 102", "municipal form no 102", "registry no", "registered at the office of the civil registrar"],
                "min_hits": 1,
                "fuzzy": {"threshold": 84, "min_hits": 1, "per_line": False, "keywords": ["CERTIFICATE OF LIVE BIRTH", "OFFICE OF THE CIVIL REGISTRAR GENERAL", "MUNICIPAL FORM NO 102", "REGISTERED AT THE OFFICE OF THE CIVIL REGISTRAR"]},
            },
            "2": {
                "enabled": True,
                "strong": [],
                "keywords": ["affidavit of acknowledgment", "admission of paternity", "affidavit for delayed registration of birth", "subscribed and sworn"],
                "min_hits": 1,
                "fuzzy": {"threshold": 82, "min_hits": 1, "per_line": False, "keywords": ["AFFIDAVIT OF ACKNOWLEDGMENT", "ADMISSION OF PATERNITY", "AFFIDAVIT FOR DELAYED REGISTRATION OF BIRTH", "SUBSCRIBED AND SWORN"]},
            },
        },
    },
    "OPR": {
        "enabled": True,
        "priority": 90,
        "keywords": ["operating room record", "delivery room record"],
        "aliases": [],
        "suppress_if": [],
        "fuzzy": {"threshold": 88, "min_hits": 1, "per_line": False, "keywords": ["OPERATING ROOM RECORD", "DELIVERY ROOM RECORD"]},
    },
    "ANR": {
        "enabled": True,
        "priority": 85,
        "strong": ["anesthesia record", "anaesthesia record"],
        "keywords": ["premedication", "proposed operation", "anesthetic agent", "anaesthetic agent", "detailed technique", "induction", "maintenance", "emergence", "fluid summary", "urine output in o.r", "urine output in o r", "condition of patient on departure"],
        "min_hits": 2,
        "aliases": [],
        "suppress_if": [],
        "fuzzy": {"threshold": 78, "min_hits": 1, "per_line": False, "keywords": ["ANESTHESIA RECORD", "ANAESTHESIA RECORD", "PREMEDICATION DOSE ROUTE TIME", "PROPOSED OPERATION", "ANESTHETIC AGENT", "DETAILED TECHNIQUE", "CONDITION OF PATIENT ON DEPARTURE"]},
    },
    "CF2": {
        "enabled": True,
        "priority": 80,
        "keywords": [],
        "aliases": [],
        "suppress_if": [],
        "page_rules": {
            "1": {
                "enabled": True,
                "strong": ["type of accommodation", "for essential newborn care", "essential newborn care"],
                "keywords": ["claim form 2", "type of accommodation", "z-benefit package code", "for essential newborn care", "essential newborn care"],
                "min_hits": 2,
            },
            "2": {
                "enabled": True,
                "strong": [],
                "keywords": ["certification of consumption", "no co-pay on top", "with co-pay on top", "name of accredited health care professional", "authorized hci representative", "accreditation number"],
                "min_hits": 2,
            },
        },
    },
    "CSF": {
        "enabled": True,
        "priority": 75,
        "keywords": ["claim signature form", "cl signature form", "this form may be reproduced", "csf"],
        "aliases": [],
        "suppress_if": [],
    },
    "COE": {
        "enabled": True,
        "priority": 70,
        "keywords": ["hci portal reference no", "hci portal reference", "philhealth benefit eligibility", "philhealth benefit eligibility form", "teamphilhealth", "team philhealth"],
        "aliases": [],
        "suppress_if": [],
    },
    "DTR": {
        "enabled": True,
        "priority": 65,
        "strong": [],
        "keywords": ["x-ray", "xray", "ct scan", "ultrasound", "hematology", "urinalysis", "diagnostic", "examination", "mila amor", "electrocardiogram", "electro", "ecg", "normal sinus rhythm", "laboratory result", "clinical chemistry", "cross-matching", "parasitology", "2d echocardiography", "2-d echocardiogram", "2d echocardiogram", "echocardiography", "echocardiogram", "blood bank result", "laboratory department", "blood typing", "blood chemistry", "serology", "radiographic report", "reference range", "ref range", "creatinine", "hemoglobin", "platelet", "wbc", "rbc", "blood type", "cross matching", "ventricular rate", "pr interval", "qrs duration", "qt qtc"],
        "min_hits": 1,
        "aliases": [],
        "suppress_if": ["please pay at the cashier", "soa reference no", "soa reference #", "soa ref no", "soa ref #", "statement of account", "patient's statement of account", "summary of fees", "summary of charges", "itemized charges"],
    },
    "SOA1": {
        "enabled": True,
        "priority": 60,
        "keywords": ["please pay at the cashier"],
        "aliases": [],
        "suppress_if": [],
        "fuzzy": {"threshold": 82, "min_hits": 1, "per_line": True, "keywords": ["PLEASE PAY AT THE CASHIER"]},
    },
    "SOA2": {
        "enabled": True,
        "priority": 55,
        "keywords": [],
        "aliases": [],
        "suppress_if": [],
        "page_rules": {
            "1": {
                "enabled": True,
                "strong": ["soa reference no", "soa reference #", "soa ref no", "soa ref #", "statement of account"],
                "keywords": ["print name", "patient name", "date and time admitted", "date and time discharged", "final diagnosis", "summary of fees", "itemized charges"],
                "min_hits": 5,
            },
            "2": {
                "enabled": True,
                "strong": ["conforme", "prepared by"],
                "keywords": ["relationship of representative", "patient / representative", "patient/representative", "patient representative", "signature over printed name", "administrative officer", "administrative aide"],
                "min_hits": 1,
                "fuzzy": {"threshold": 76, "min_hits": 1, "per_line": True, "keywords": ["RELATIONSHIP OF REPRESENTATIVE", "PATIENT REPRESENTATIVE", "SIGNATURE OVER PRINTED NAME", "ADMINISTRATIVE OFFICER"]},
            },
            "single": {
                "enabled": True,
                "strong": [],
                "keywords": ["soa reference no", "soa reference #", "soa ref no", "soa ref #", "statement of account", "conforme", "prepared by", "signature over printed name", "patient / representative", "patient/representative", "patient representative", "relationship of representative", "administrative officer", "administrative aide"],
                "min_hits": 3,
            },
        },
    },
    "MMC": {
        "enabled": True,
        "priority": 20,
        "keywords": ["certificate of marriage", "marriage certificate", "municipal form no. 97", "office of the civil registrar", "local civil registrar"],
        "aliases": [],
        "suppress_if": [],
    },
}


def default_rules() -> Dict[str, Dict[str, Any]]:
    """Deep copy of DEFAULT_RULES (Restore Defaults / missing file)."""
    return copy.deepcopy(DEFAULT_RULES)

# ---------------------------------------------------------------
# Validation / persistence (malformed rules must never crash claims
# processing - invalid entries are dropped with a clear message)
# ---------------------------------------------------------------

def _clean_string_list(value: Any, errors: List[str], label: str) -> List[str]:
    cleaned: List[str] = []
    if value is None:
        return cleaned
    if not isinstance(value, list):
        errors.append(f"{label}: expected a list")
        return cleaned
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        if len(text) > MAX_KEYWORD_LENGTH:
            text = text[:MAX_KEYWORD_LENGTH]
        if text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= MAX_KEYWORDS_PER_LIST:
            errors.append(f"{label}: more than {MAX_KEYWORDS_PER_LIST} entries - extra entries dropped")
            break
    return cleaned


def _validate_fuzzy(raw: Any, errors: List[str], label: str) -> Optional[Dict[str, Any]]:
    if raw in (None, {}, ""):
        return None
    if not isinstance(raw, dict):
        errors.append(f"{label}.fuzzy: expected an object")
        return None
    try:
        threshold = int(raw.get("threshold"))
    except (TypeError, ValueError):
        errors.append(f"{label}.fuzzy.threshold: invalid number")
        return None
    if not 50 <= threshold <= 99:
        errors.append(f"{label}.fuzzy.threshold: must be 50..99")
        return None
    keywords = _clean_string_list(raw.get("keywords"), errors, f"{label}.fuzzy.keywords")
    if not keywords:
        return None
    try:
        min_hits = max(1, int(raw.get("min_hits", 1)))
    except (TypeError, ValueError):
        min_hits = 1
    return {"threshold": threshold, "min_hits": min_hits, "per_line": bool(raw.get("per_line", False)), "keywords": keywords}


def _validate_level_spec(raw: Any, errors: List[str], label: str) -> Dict[str, Any]:
    """Validate one rule or page-rule level."""
    spec: Dict[str, Any] = {"enabled": True, "strong": [], "keywords": [], "aliases": [], "suppress_if": [], "min_hits": 1}
    if raw is None:
        return spec
    if not isinstance(raw, dict):
        errors.append(f"{label}: expected an object")
        return spec
    spec["enabled"] = bool(raw.get("enabled", True))
    spec["strong"] = _clean_string_list(raw.get("strong"), errors, f"{label}.strong")
    spec["keywords"] = _clean_string_list(raw.get("keywords"), errors, f"{label}.keywords")
    spec["aliases"] = _clean_string_list(raw.get("aliases"), errors, f"{label}.aliases")
    spec["suppress_if"] = _clean_string_list(raw.get("suppress_if"), errors, f"{label}.suppress_if")
    try:
        spec["min_hits"] = max(1, min(100, int(raw.get("min_hits", 1))))
    except (TypeError, ValueError):
        errors.append(f"{label}.min_hits: invalid number (defaulted to 1)")
    fuzzy = _validate_fuzzy(raw.get("fuzzy"), errors, label)
    if fuzzy:
        spec["fuzzy"] = fuzzy
    return spec


def validate_rules(data: Any) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Validate a document_types dict; invalid rules are dropped with errors."""
    errors: List[str] = []
    cleaned: Dict[str, Dict[str, Any]] = {}
    if not isinstance(data, dict):
        errors.append("document_types: expected an object")
        return cleaned, errors
    for doc_type, raw in data.items():
        type_code = str(doc_type or "").strip().upper()
        if not _TYPE_RE.match(type_code):
            errors.append(f"{doc_type!r}: invalid document type code (skipped)")
            continue
        if type_code in cleaned:
            errors.append(f"{type_code}: duplicate document type (skipped)")
            continue
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            errors.append(f"{type_code}: expected an object (skipped)")
            continue
        rule = _validate_level_spec(raw, errors, type_code)
        try:
            rule["priority"] = max(-1000, min(1000, int(raw.get("priority", 50))))
        except (TypeError, ValueError):
            errors.append(f"{type_code}.priority: invalid number (defaulted to 50)")
            rule["priority"] = 50
        page_rules_raw = raw.get("page_rules") or {}
        page_rules: Dict[str, Dict[str, Any]] = {}
        if isinstance(page_rules_raw, dict):
            for page_key, page_raw in page_rules_raw.items():
                key = str(page_key or "").strip().lower()
                if key != "single" and (not _PAGE_RE.match(key) or int(key) < 1):
                    errors.append(f"{type_code}.page_rules[{page_key!r}]: invalid page key (skipped)")
                    continue
                page_rules[key] = _validate_level_spec(page_raw, errors, f"{type_code}.page_rules[{key}]")
        else:
            errors.append(f"{type_code}.page_rules: expected an object")
        if page_rules:
            rule["page_rules"] = page_rules
        cleaned[type_code] = rule
    return cleaned, errors


def load_rules(path: Path = RULES_FILE) -> Tuple[Optional[Dict[str, Dict[str, Any]]], str]:
    """Load rules from disk. Returns (document_types_or_None, error).

    None + empty error -> file missing (first run; caller uses defaults).
    None + error text  -> corrupt/unreadable or unsupported schema; the caller
    must log it and fall back to the built-in defaults (never misclassify,
    never silently switch detection mode).
    """
    path = Path(path)
    if not path.is_file():
        return None, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"document_detection_rules.json is unreadable/corrupt: {exc}"
    if not isinstance(data, dict):
        return None, "document_detection_rules.json: expected a JSON object"
    if data.get("schema_version") != SCHEMA_VERSION:
        return None, (
            f"document_detection_rules.json: unsupported schema_version {data.get('schema_version')!r} "
            f"(expected {SCHEMA_VERSION})"
        )
    document_types, errors = validate_rules(data.get("document_types"))
    if errors:
        return document_types, "; ".join(errors)
    return document_types, ""


def save_rules(document_types: Dict[str, Dict[str, Any]], path: Path = RULES_FILE) -> str:
    """Validate + persist rules. Returns an error message ('' on success)."""
    cleaned, errors = validate_rules(document_types)
    message = "; ".join(errors)
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"schema_version": SCHEMA_VERSION, "document_types": cleaned}, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        return (message + "; " if message else "") + f"could not save rules: {exc}"
    _invalidate_cache()
    return message


def restore_defaults(path: Path = RULES_FILE) -> str:
    """Write the built-in default rules to disk (the hardcoded engine rules
    themselves are never modified by this)."""
    return save_rules(default_rules(), path)


def _invalidate_cache() -> None:
    _RULES_CACHE["mtime"] = None
    _RULES_CACHE["types"] = None
    _RULES_CACHE["error"] = ""


def _get_rules() -> Tuple[Dict[str, Dict[str, Any]], str]:
    """Load rules once per session (mtime-checked, never per keyword)."""
    try:
        mtime = RULES_FILE.stat().st_mtime if RULES_FILE.is_file() else None
    except OSError:
        mtime = None
    # The cache starts as {"mtime": None, "types": None} and a missing rules
    # file also yields mtime=None, so "types is None" must force a reload.
    # Otherwise the very first call with no document_detection_rules.json on
    # disk returned the empty initial cache and EVERY page detected as
    # UNKNOWN whenever the configurable mode was enabled (2026-09-23 bug).
    if _RULES_CACHE["mtime"] != mtime or _RULES_CACHE["types"] is None:
        if mtime is None:
            document_types, error = None, ""
        else:
            document_types, error = load_rules()
        if document_types is None:
            document_types = default_rules()
        _RULES_CACHE["mtime"] = mtime
        _RULES_CACHE["types"] = document_types
        _RULES_CACHE["error"] = error
    return _RULES_CACHE["types"], _RULES_CACHE["error"]

# ---------------------------------------------------------------
# Matching / detection (deterministic keyword matching only)
# ---------------------------------------------------------------

def _hit(keyword: str, text_lower: str, clean: str) -> bool:
    needle = str(keyword or "").strip().lower()
    if not needle:
        return False
    if needle in text_lower:
        return True
    return normalize_text(needle) in clean


def _first_hit(keywords: Any, text_lower: str, clean: str) -> Optional[str]:
    for keyword in keywords or []:
        if _hit(keyword, text_lower, clean):
            return str(keyword)
    return None


def _fuzzy_hit(fuzzy: Dict[str, Any], clean: str, raw_text: str) -> Optional[str]:
    if fuzz is None:
        return None
    try:
        threshold = int(fuzzy.get("threshold", 0) or 0)
        min_hits = max(1, int(fuzzy.get("min_hits", 1) or 1))
    except (TypeError, ValueError):
        return None
    if threshold < 50:
        return None
    per_line = bool(fuzzy.get("per_line", False))
    lines = [normalize_text(line) for line in str(raw_text or "").splitlines()]
    lines = [line for line in lines if line]
    hits = 0
    for phrase in fuzzy.get("keywords", []):
        phrase_clean = str(phrase or "").strip().upper()
        if not phrase_clean:
            continue
        if per_line:
            matched = any(fuzz.partial_ratio(phrase_clean, line) >= threshold for line in lines)
        else:
            matched = fuzz.partial_ratio(phrase_clean, clean) >= threshold
        if matched:
            hits += 1
            if hits >= min_hits:
                return phrase_clean
    return None


def _level_match(spec: Dict[str, Any], text_lower: str, clean: str, raw_text: str) -> Optional[Dict[str, Any]]:
    """Return match diagnostics when a rule/page level matches, else None."""
    if _first_hit(spec.get("suppress_if"), text_lower, clean) is not None:
        return None
    matched = _first_hit(spec.get("strong"), text_lower, clean)
    if matched is not None:
        return {"matched_by": "strong", "keyword": matched}
    matched_keyword = None
    hits = 0
    for keyword in list(spec.get("keywords") or []) + list(spec.get("aliases") or []):
        if _hit(keyword, text_lower, clean):
            hits += 1
            if matched_keyword is None:
                matched_keyword = str(keyword)
            if hits >= int(spec.get("min_hits", 1) or 1):
                return {"matched_by": "keywords", "keyword": matched_keyword, "hits": hits}
    fuzzy = spec.get("fuzzy")
    if isinstance(fuzzy, dict):
        phrase = _fuzzy_hit(fuzzy, clean, raw_text)
        if phrase is not None:
            return {"matched_by": "fuzzy", "keyword": phrase}
    return None


def _page_rules_match(rule: Dict[str, Any], text_lower: str, clean: str, raw_text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Evaluate page rules in configured order (\"single\" always last).

    When two or more numeric page rules match and a \"single\" rule exists,
    the plain type is returned (complete form, e.g. SOA2).
    """
    page_rules = rule.get("page_rules") or {}
    single = page_rules.get("single")
    single = single if isinstance(single, dict) and single.get("enabled", True) else None
    matched_numeric: List[Tuple[str, Dict[str, Any]]] = []
    for page_key, spec in page_rules.items():
        key = str(page_key)
        if key == "single":
            continue
        if not isinstance(spec, dict) or not spec.get("enabled", True):
            continue
        diagnostic = _level_match(spec, text_lower, clean, raw_text)
        if diagnostic is not None:
            matched_numeric.append((key, diagnostic))
    if len(matched_numeric) >= 2 and single is not None:
        return "", {"matched_by": "page_complete", "keyword": "", "pages": [key for key, _ in matched_numeric]}
    if matched_numeric:
        return matched_numeric[0]
    if single is not None:
        diagnostic = _level_match(single, text_lower, clean, raw_text)
        if diagnostic is not None:
            return "", diagnostic
    return None


def _rule_matches(rule: Dict[str, Any], text_lower: str, clean: str, raw_text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    if _first_hit(rule.get("suppress_if"), text_lower, clean) is not None:
        return None
    if rule.get("page_rules"):
        return _page_rules_match(rule, text_lower, clean, raw_text)
    diagnostic = _level_match(rule, text_lower, clean, raw_text)
    if diagnostic is None:
        return None
    return "", diagnostic


def detect_doc_configurable(text: str, rules: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """THE configurable detector - only runs when Document Detection Rules
    Configurable is ON.  Returns the same document-type contract as the
    hardcoded detect_doc(): <TYPE>, <TYPE>_page1/_page2/... or \"UNKNOWN\".
    Never raises; config problems degrade to defaults + a logged error and
    never to hardcoded detection (the mode is never silently changed)."""
    global _LAST_DIAGNOSTICS
    raw_text = str(text or "")
    text_lower = raw_text.lower()
    clean = normalize_text(raw_text)

    if rules is None:
        document_types, config_error = _get_rules()
    else:
        document_types, errors = validate_rules(rules)
        config_error = "; ".join(errors)
    if config_error:
        print("[DOC DETECTION CONFIG ERROR]", config_error)

    matches: List[Tuple[int, int, str, str, Dict[str, Any]]] = []
    order = 0
    for doc_type, rule in (document_types or {}).items():
        if not isinstance(rule, dict) or not rule.get("enabled", True):
            continue
        order += 1
        result = _rule_matches(rule, text_lower, clean, raw_text)
        if result is None:
            continue
        page_key, diagnostic = result
        try:
            priority = int(rule.get("priority", 50) or 0)
        except (TypeError, ValueError):
            priority = 0
        matches.append((priority, order, doc_type, page_key, diagnostic))

    if not matches:
        _LAST_DIAGNOSTICS = {"doc_type": "UNKNOWN", "matched_rule": "", "matched_keyword": "", "page": "", "ambiguous": False, "config_error": config_error}
        return "UNKNOWN"

    matches.sort(key=lambda item: (-item[0], item[1]))
    best = matches[0]
    tied = [item for item in matches if item[0] == best[0]]
    if len(tied) > 1:
        candidates = ", ".join(
            item[2] + (f"_page{item[3]}" if item[3] else "") for item in tied
        )
        print("[DOC DETECTION AMBIGUOUS] priority", best[0], "->", candidates, "= UNKNOWN (existing review flow)")
        _LAST_DIAGNOSTICS = {"doc_type": "UNKNOWN", "matched_rule": candidates, "matched_keyword": best[4].get("keyword", ""), "page": "", "ambiguous": True, "config_error": config_error}
        return "UNKNOWN"

    priority, _, doc_type, page_key, diagnostic = best
    result_type = doc_type + (f"_page{page_key}" if page_key else "")
    print(
        "[DOC DETECTION RULE]", result_type,
        f"rule={doc_type}",
        f"matched_by={diagnostic.get('matched_by', '')}",
        f"keyword={diagnostic.get('keyword', '')}",
        f"priority={priority}",
    )
    _LAST_DIAGNOSTICS = {
        "doc_type": result_type,
        "matched_rule": doc_type,
        "matched_keyword": diagnostic.get("keyword", ""),
        "page": page_key,
        "ambiguous": False,
        "config_error": config_error,
    }
    return result_type


def last_diagnostics() -> Dict[str, Any]:
    """Diagnostics of the most recent detect_doc_configurable() call."""
    return dict(_LAST_DIAGNOSTICS)

if __name__ == "__main__":
    rules = default_rules()
    samples = [
        ("CLAIM SIGNATURE FORM", "CSF"),
        # Regression (2026-09-23 CSF bug): real CSF page-1 OCR always contains
        # "philhealth identification number" (Part I).  With the old MRF
        # page-1 rule (context keywords, min_hits=1) that single hit made
        # MRF_page1 win over CSF.  UHC-gated MRF keywords must not match.
        ("THIS FORM MAY BE REPRODUCED REPUBLIC OF THE PHILIPPINES PHILIPPINE HEALTH INSURANCE CORPORATION (CLAIM SIGNATURE FORM) 1. PHILHEALTH IDENTIFICATION NUMBER (PIN) OF MEMBER: 2. DATE OF BIRTH 3. SEX", "CSF"),
        # MRF page 1 via the hardcoded UHC + context path must still match.
        ("UHC UNIVERSAL HEALTH CARE PERSONAL DETAILS REGISTRATION MAIDEN NAME", "MRF_page1"),
        ("PHILHEALTH MEMBER REGISTRATION FORM PHILSYS", "MRF_page1"),
        ("FOR PHILHEALTH USE ONLY UPDATING/AMENDMENT CHANGE/CORRECTION OF NAME", "MRF_page2"),
        ("CLAIM FORM 2 TYPE OF ACCOMMODATION Z-BENEFIT PACKAGE CODE", "CF2_page1"),
        ("CERTIFICATION OF CONSUMPTION AUTHORIZED HCI REPRESENTATIVE", "CF2_page2"),
        ("HCI PORTAL REFERENCE NO 12345 PHILHEALTH BENEFIT ELIGIBILITY", "COE"),
        ("HEMATOLOGY REFERENCE RANGE LABORATORY RESULT CLINICAL CHEMISTRY", "DTR"),
        ("PLEASE PAY AT THE CASHIER", "SOA1"),
        ("STATEMENT OF ACCOUNT SOA REFERENCE NO 2026 PRINT NAME FINAL DIAGNOSIS SUMMARY OF FEES", "SOA2_page1"),
        ("PREPARED BY CONFORME SIGNATURE OVER PRINTED NAME", "SOA2_page2"),
        ("STATEMENT OF ACCOUNT SOA REFERENCE NO 2026 PREPARED BY CONFORME", "SOA2"),
        ("ANESTHESIA RECORD PREMEDICATION INDUCTION", "ANR"),
        ("OPERATING ROOM RECORD", "OPR"),
        ("CERTIFICATE OF LIVE BIRTH OFFICE OF THE CIVIL REGISTRAR GENERAL", "PBC_page1"),
        ("AFFIDAVIT OF ACKNOWLEDGMENT SUBSCRIBED AND SWORN", "PBC_page2"),
        ("CERTIFICATE OF MARRIAGE LOCAL CIVIL REGISTRAR", "MMC"),
        ("HELLO WORLD NOTHING HERE", "UNKNOWN"),
    ]
    failures = 0
    for sample, expected in samples:
        got = detect_doc_configurable(sample, rules=rules)
        ok = got == expected
        failures += 0 if ok else 1
        print(f"[{('PASS' if ok else 'FAIL')}] {expected} <- {sample[:55]!r} (got {got})")
    # Owner test 47 (hardcoded bypass proof): configurable COE rule without
    # the COE keywords must NOT classify a COE document when mode is ON.
    bypass = copy.deepcopy(rules)
    bypass["COE"]["enabled"] = False
    got = detect_doc_configurable("HCI PORTAL REFERENCE NO 12345", rules=bypass)
    bypass_ok = got == "UNKNOWN"
    failures += 0 if bypass_ok else 1
    print(f"[{'PASS' if bypass_ok else 'FAIL'}] disabled COE rule -> UNKNOWN (got {got})")
    # Ambiguity: two different rules with the SAME priority
    ambiguous = copy.deepcopy(rules)
    ambiguous["CSF"]["priority"] = 100
    ambiguous["MRF"]["priority"] = 100
    got = detect_doc_configurable("PHILHEALTH MEMBER REGISTRATION FORM CLAIM SIGNATURE FORM", rules=ambiguous)
    ambiguous_ok = got == "UNKNOWN"
    failures += 0 if ambiguous_ok else 1
    print(f"[{'PASS' if ambiguous_ok else 'FAIL'}] exact priority tie -> UNKNOWN (got {got})")
    # Regression (2026-09-23): with NO rules file on disk, the very first
    # _get_rules() call must fall back to the built-in defaults.  The cache
    # sentinel (mtime=None) previously collided with the "file missing"
    # state, so enabling the mode before any document_detection_rules.json
    # existed produced an empty rule set and every page was UNKNOWN.
    import tempfile

    real_rules_file = RULES_FILE
    _invalidate_cache()
    RULES_FILE = Path(tempfile.mkdtemp()) / "missing_rules.json"
    got = detect_doc_configurable("CLAIM SIGNATURE FORM")
    missing_file_ok = got == "CSF"
    failures += 0 if missing_file_ok else 1
    print(f"[{'PASS' if missing_file_ok else 'FAIL'}] missing rules file -> defaults fallback (got {got})")
    RULES_FILE = real_rules_file
    _invalidate_cache()
    print("RESULT:", "PASSED" if failures == 0 else f"{failures} FAILURE(S)")
    raise SystemExit(0 if failures == 0 else 1)
