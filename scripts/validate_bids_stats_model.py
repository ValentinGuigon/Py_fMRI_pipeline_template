#!/usr/bin/env python3
"""
Validate a BIDS Stats Models JSON file.

Behavior:
1) If bsmschema is available:
   - validate using bsmschema.models.BIDSStatsModel
2) Otherwise (Python 3.6-safe fallback):
   - validate JSON syntax
   - run a conservative set of structural checks and report errors

Usage:
  python3 validate_bids_stats_model.py /path/to/model.json
  python3 validate_bids_stats_model.py /path/to/model.json --json

Exit codes:
  0 = OK
  1 = invalid (schema/structure)
  2 = file not found / unreadable
"""

from __future__ import print_function

import argparse
import json
import sys
from pathlib import Path


def _print_errors(errors, as_json=False):
    if as_json:
        print(json.dumps(errors, indent=2, sort_keys=True))
    else:
        print("INVALID")
        for e in errors:
            loc = e.get("loc", "")
            msg = e.get("msg", "")
            print("- {0}: {1}".format(loc, msg))


def _load_json(path):
    try:
        txt = path.read_text()
    except Exception:
        # Python 3.6: Path.read_text exists, but keep safe
        with open(str(path), "r") as f:
            txt = f.read()
    return json.loads(txt)


def _is_nonempty_str(x):
    return isinstance(x, str) and len(x.strip()) > 0


def fallback_validate(model):
    """
    Minimal structural validator (NOT the full official schema).
    Designed to be useful when bsmschema isn't available.
    """
    errors = []

    if not isinstance(model, dict):
        return [{"loc": "$", "msg": "Top-level JSON must be an object/dict"}]

    # Required top-level keys (lightweight)
    if not _is_nonempty_str(model.get("Name", "")):
        errors.append({"loc": "Name", "msg": "Missing or empty string"})
    if not _is_nonempty_str(model.get("BIDSModelVersion", "")):
        errors.append({"loc": "BIDSModelVersion", "msg": "Missing or empty string"})
    if "Nodes" not in model or not isinstance(model["Nodes"], list) or len(model["Nodes"]) == 0:
        errors.append({"loc": "Nodes", "msg": "Must be a non-empty list"})
        return errors  # can't go further

    allowed_levels = set(["Run", "Session", "Subject", "Dataset"])

    for i, node in enumerate(model["Nodes"]):
        loc0 = "Nodes[{0}]".format(i)
        if not isinstance(node, dict):
            errors.append({"loc": loc0, "msg": "Node must be an object/dict"})
            continue

        level = node.get("Level", None)
        name = node.get("Name", None)

        if level not in allowed_levels:
            errors.append({"loc": loc0 + ".Level", "msg": "Missing/invalid Level (expected one of {0})".format(sorted(allowed_levels))})
        if not _is_nonempty_str(name or ""):
            errors.append({"loc": loc0 + ".Name", "msg": "Missing/empty Name"})

        # GroupBy is common; not always required for all nodes, but should be list if present
        if "GroupBy" in node and not isinstance(node["GroupBy"], list):
            errors.append({"loc": loc0 + ".GroupBy", "msg": "If present, must be a list"})

        # Transformations can be list or dict depending on ecosystem; just sanity-check type
        if "Transformations" in node:
            tr = node["Transformations"]
            if tr is not None and not isinstance(tr, (list, dict)):
                errors.append({"loc": loc0 + ".Transformations", "msg": "Must be a list, dict, or null"})

        # Model presence: expected for Run/Dataset; Subject often has Model for meta; but FitLins is picky.
        if level in ("Run", "Dataset", "Subject"):
            if "Model" not in node or not isinstance(node["Model"], dict):
                errors.append({"loc": loc0 + ".Model", "msg": "Missing Model object"})
            else:
                m = node["Model"]
                # Type is important for many implementations
                if "Type" in m and not _is_nonempty_str(m.get("Type", "")):
                    errors.append({"loc": loc0 + ".Model.Type", "msg": "If present, must be a non-empty string"})
                # X often present; if present must be list
                if "X" in m and not isinstance(m["X"], list):
                    errors.append({"loc": loc0 + ".Model.X", "msg": "If present, must be a list"})
        # Contrasts: if present, check structure
        if "Contrasts" in node:
            if not isinstance(node["Contrasts"], list):
                errors.append({"loc": loc0 + ".Contrasts", "msg": "Must be a list"})
            else:
                for j, c in enumerate(node["Contrasts"]):
                    locc = loc0 + ".Contrasts[{0}]".format(j)
                    if not isinstance(c, dict):
                        errors.append({"loc": locc, "msg": "Contrast must be an object/dict"})
                        continue
                    if not _is_nonempty_str(c.get("Name", "")):
                        errors.append({"loc": locc + ".Name", "msg": "Missing/empty Name"})
                    if "ConditionList" in c and not isinstance(c["ConditionList"], list):
                        errors.append({"loc": locc + ".ConditionList", "msg": "If present, must be a list"})
                    if "Weights" in c and not isinstance(c["Weights"], list):
                        errors.append({"loc": locc + ".Weights", "msg": "If present, must be a list"})
                    if "Test" in c and not _is_nonempty_str(c.get("Test", "")):
                        errors.append({"loc": locc + ".Test", "msg": "If present, must be a non-empty string"})

    return errors


def main():
    ap = argparse.ArgumentParser(description="Validate a BIDS Stats Models JSON file.")
    ap.add_argument("model", type=str, help="Path to model JSON file")
    ap.add_argument("--json", action="store_true", help="Print errors as JSON")
    args = ap.parse_args()

    path = Path(args.model)
    if not path.exists():
        print("ERROR: file not found: {0}".format(path), file=sys.stderr)
        return 2

    # Load JSON
    try:
        model = _load_json(path)
    except Exception as e:
        _print_errors([{"loc": "$", "msg": "Invalid JSON: {0}".format(e)}], as_json=args.json)
        return 1

    # Try bsmschema if available
    try:
        from bsmschema.models import BIDSStatsModel  # noqa: F401
    except Exception:
        # fallback validation
        errs = fallback_validate(model)
        if errs:
            _print_errors(errs, as_json=args.json)
            return 1
        print("OK (fallback checks only; bsmschema not installed)")
        return 0

    # bsmschema validation
    try:
        from bsmschema.models import BIDSStatsModel
        BIDSStatsModel.parse_obj(model)
    except Exception as e:
        # pydantic-like error formatting if possible
        if hasattr(e, "errors") and callable(getattr(e, "errors")):
            errs = [{"loc": ".".join(str(x) for x in er.get("loc", [])),
                     "msg": er.get("msg", ""),
                     "type": er.get("type", "")}
                    for er in e.errors()]
            _print_errors(errs, as_json=args.json)
        else:
            _print_errors([{"loc": "$", "msg": str(e)}], as_json=args.json)
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
