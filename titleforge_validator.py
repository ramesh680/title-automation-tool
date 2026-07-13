"""
titleforge_validator.py
=======================

Loads titleforge_validation_rules.json and validates a row against its schema.
Returns findings shaped like the Review tab already uses:

    {"field": <col>, "status": "gap"|"mismatch"|"ok", "value": <cell>,
     "expected": <what it should be>, "msg": <human message>}

Pairs with titleforge_ingest_ext.py (detect_schema / fill_category).

Usage:
    from titleforge_ingest_ext import detect_schema, fill_category
    from titleforge_validator import load_rules, validate_row

    RULES = load_rules("titleforge_validation_rules.json")
    schema = detect_schema(row) or "general"
    findings = validate_row(row, schema, RULES)
"""

from __future__ import annotations
import json, re
from typing import Any, Dict, List, Optional

# reuse the exact same helpers as the ingest module
from titleforge_ingest_ext import (
    _get, _is_standard, _hashtag, GENERAL_TITLE_CATEGORIES,
)


def load_rules(path: str = "titleforge_validation_rules.json") -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _finding(field, status, value="", expected="", msg=""):
    return {"field": field, "status": status, "value": value,
            "expected": expected, "msg": msg}


# reviewer feedback (Jul 2026): 'True' and 't' are both correct
_BOOL_TRUE = {"t", "true", "yes", "y", "1"}
_BOOL_FALSE = {"f", "false", "no", "n", "0"}


def _bool_equal(a: str, b: str) -> bool:
    a, b = str(a).strip().lower(), str(b).strip().lower()
    return (a in _BOOL_TRUE and b in _BOOL_TRUE) or \
           (a in _BOOL_FALSE and b in _BOOL_FALSE)


def validate_row(row: Dict[str, Any], schema_key: str,
                 rules: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate one row; return only non-ok findings (gaps + mismatches)."""
    schema = rules["schemas"][schema_key]
    master = rules.get("GENERAL_TITLE_CATEGORIES", GENERAL_TITLE_CATEGORIES)
    out: List[Dict[str, Any]] = []

    for rule in schema["rules"]:
        field = rule["field"]
        val = _get(row, field, field.replace("_", " "))
        t = rule["type"]

        if t == "required":
            if not val:
                out.append(_finding(field, "gap", "", "<non-empty>", rule["msg"]))

        elif t == "const":
            if not val:
                out.append(_finding(field, "gap", "", rule["value"], rule["msg"]))
            elif val != rule["value"] and not _bool_equal(val, rule["value"]):
                # 'True' == 't', 'False' == 'f', etc. are equivalent
                out.append(_finding(field, "mismatch", val, rule["value"], rule["msg"]))

        elif t == "enum":
            if val and val not in rule["values"]:
                # brand_set can be updated by anyone -- extra values are fine
                # as long as at least one canonical value is present
                if field == "brand_set":
                    lines = [ln.strip() for ln in val.splitlines() if ln.strip()]
                    if any(ln in rule["values"] for ln in lines):
                        continue
                out.append(_finding(field, "mismatch", val, "one of dropdown", rule["msg"]))

        elif t == "enum_ref":
            allowed = master if rule.get("ref") == "GENERAL_TITLE_CATEGORIES" else []
            if not val:
                out.append(_finding(field, "gap", "", "<pick from list>", rule["msg"]))
            elif val not in allowed:
                out.append(_finding(field, "mismatch", val, "one of master list", rule["msg"]))

        elif t == "multiline_enum":
            if val:
                allowed = set(rule["values"])
                bad = [ln for ln in val.splitlines()
                       if ln.strip() and ln.strip() not in allowed]
                if bad:
                    out.append(_finding(field, "mismatch", "; ".join(bad),
                                        "valid type/company", rule["msg"]))

        elif t == "dar_suffix":
            if val and _is_standard(row) and not val.endswith(" - DAR"):
                out.append(_finding(field, "mismatch", val, val + " - DAR", rule["msg"]))

        elif t == "companies_logic":
            if _is_standard(row) and val != rule["standard_value"]:
                out.append(_finding(field, "mismatch", val,
                                    rule["standard_value"], rule["msg"]))

        elif t == "hashtag_format":
            title = _get(row, "title", "Title", "Title Name")
            # strip the DAR suffix before deriving the expected hashtag
            base = title[:-6].strip() if title.endswith(" - DAR") else title
            expected = _hashtag(base)
            # reviewer feedback: manually curated terms are valid alternatives;
            # only flag when the value contains no #hashtag/@handle at all
            if expected and val and val != expected \
                    and not re.search(r"[#@]\w", val):
                out.append(_finding(field, "mismatch", val, expected, rule["msg"]))

    return out


if __name__ == "__main__":
    from titleforge_ingest_ext import detect_schema, fill_category
    RULES = load_rules("titleforge_validation_rules.json")

    samples = [
        # good beauty row
        {"Perspective": "Standard", "title": "Fenty Beauty - DAR",
         "title_category": "Health & Beauty",
         "title_sub_category": "Beauty Type - Makeup\nBeauty Company - LVMH",
         "brand_set": "LF // Beauty", "companies": "Pristine Brand", "active": "t",
         "twitter_search_terms": "#FentyBeauty"},
        # beverages with bad sub-category + missing DAR
        {"Perspective": "Standard", "title": "Red Bull",
         "title_category": "Beverages",
         "title_sub_category": "Beverage Type - Rocket Fuel",
         "brand_set": "LF // Beverages", "companies": "Pristine Brand", "active": "t"},
    ]
    for s in samples:
        sk = detect_schema(s) or "general"
        findings = validate_row(s, sk, RULES)
        print(f"\nschema={sk}  title={_get(s,'title')!r}  findings={len(findings)}")
        for fd in findings:
            print(f"   [{fd['status']:8}] {fd['field']}: {fd['value']!r} -> {fd['expected']!r}")
