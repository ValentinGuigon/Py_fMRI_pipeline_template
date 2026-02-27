#!/usr/bin/env python3
"""
parse_model.py
==============
Emit shell-friendly values parsed from a BIDS StatsModel JSON.
Used by run_pipeline.sh to auto-derive nodes, contrasts, stat type,
subjects, and tasks without repeating information that already lives
in the model JSON.

Usage:
  python3 parse_model.py <model.json> --field <field>

Fields:
  subjects      Space-separated subject labels from Input.subject
  tasks         Space-separated task labels from Input.task
  stat          Test type of the first explicit contrast (t or z)
  contrasts     Space-separated names of all explicit contrasts
  run_node      Name of the Run-level node (bare, no "node-" prefix)
  group_nodes   Space-separated names of all non-Run nodes (bare)
"""
import json
import sys
import argparse
from pathlib import Path


def load(path):
    with open(path) as f:
        return json.load(f)


def field_subjects(m):
    return " ".join(m.get("Input", {}).get("subject", []))


def field_tasks(m):
    return " ".join(m.get("Input", {}).get("task", []))


def field_stat(m):
    """Return the Test type of the first explicit contrast found, else 't'."""
    for node in m.get("Nodes", []):
        for c in node.get("Contrasts", []):
            return c.get("Test", "t")
    return "t"


def field_contrasts(m):
    """Return all unique explicit contrast names across all nodes."""
    seen = []
    for node in m.get("Nodes", []):
        for c in node.get("Contrasts", []):
            name = c.get("Name", "")
            if name and name not in seen:
                seen.append(name)
    return " ".join(seen)


def field_run_node(m):
    """Return the bare name of the Run-level node."""
    for node in m.get("Nodes", []):
        if node.get("Level", "").lower() == "run":
            return node.get("Name", "")
    return ""


def field_group_nodes(m):
    """Return bare names of all non-Run nodes, space-separated."""
    out = []
    for node in m.get("Nodes", []):
        if node.get("Level", "").lower() != "run":
            name = node.get("Name", "")
            if name:
                out.append(name)
    return " ".join(out)


FIELDS = {
    "subjects":    field_subjects,
    "tasks":       field_tasks,
    "stat":        field_stat,
    "contrasts":   field_contrasts,
    "run_node":    field_run_node,
    "group_nodes": field_group_nodes,
}


def main():
    ap = argparse.ArgumentParser(
        description="Emit shell variables parsed from a BIDS StatsModel JSON."
    )
    ap.add_argument("model_json", type=Path, help="Path to model JSON file")
    ap.add_argument(
        "--field",
        choices=list(FIELDS.keys()),
        required=True,
        help="Which field to emit",
    )
    args = ap.parse_args()

    if not args.model_json.exists():
        sys.stderr.write("ERROR: model JSON not found: {}\n".format(args.model_json))
        sys.exit(1)

    m = load(args.model_json)
    result = FIELDS[args.field](m)
    print(result)


if __name__ == "__main__":
    main()