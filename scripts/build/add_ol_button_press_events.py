#!/usr/bin/env python3
"""
ol_add_button_press_events.py
==========================
Creates a directory of events TSVs with button press delta events added.

OL RULE
-------
For obslearn, button_press is derived on play/self-choice trials as:

  onset = onset + choice_RT

A hand-agnostic button_press row is always added for valid responses.
If choice_keys is present, hand-specific rows are also added:
  button_press_left
  button_press_right
"""
import argparse
import ast
import sys
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    sys.exit("ERROR: pandas is required.  pip install pandas")


def parse_list_column(value):
    """Parse a stringified Python list or scalar.

    Examples:
      '[0.317, 0.684]' -> [0.317, 0.684]
      "['left']"       -> ['left']
      0.317            -> [0.317]
      '0.317'          -> [0.317]
      NaN / bad value  -> []
    """
    if pd.isna(value):
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, (int, float)):
        return [value]

    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "[]"):
        return []

    try:
        result = ast.literal_eval(s)
        if isinstance(result, list):
            return result
        return [result]
    except Exception:
        # fallback: treat as scalar string
        try:
            return [float(s)]
        except Exception:
            return []


def get_first_numeric(value):
    """Return first numeric value from scalar or list-like cell, else None."""
    vals = parse_list_column(value)
    if not vals:
        return None
    try:
        return float(vals[0])
    except Exception:
        return None


def normalise_key(key):
    """Return 'left', 'right', or None."""
    if key is None or pd.isna(key):
        return None

    # handle either scalar string or list-like string
    vals = parse_list_column(key)
    if vals:
        k = str(vals[0]).strip().lower()
    else:
        k = str(key).strip().lower()

    if "left" in k:
        return "left"
    if "right" in k:
        return "right"
    return None


def process_tsv(src_path: Path, dst_path: Path, dry_run: bool) -> int:
    df = pd.read_csv(src_path, sep="\t")

    required = {
        "onset",
        "choice_RT",
        "responded",
    }
    missing = required - set(df.columns)
    if missing:
        print(f"[WARN] {src_path.name}: skipping, missing columns {missing}")
        return 0

    new_rows = []
    n_skipped = 0

    # responded may be numeric or string
    resp_rows = df[df["responded"].astype(str).isin(["1", "1.0", "True", "true"])]

    if len(resp_rows) == 0:
        print(f"[WARN] {src_path.name}: no responded==1 rows found")
        return 0

    for _, row in resp_rows.iterrows():
        t0 = get_first_numeric(row["onset"])
        rt = get_first_numeric(row["choice_RT"])

        if t0 is None or rt is None:
            n_skipped += 1
            continue

        onset = round(t0 + rt, 6)

        base = row.copy()
        base["onset"] = onset
        base["duration"] = 0.0

        # hand-agnostic event
        base["trial_type"] = "button_press"
        new_rows.append(base.copy())

        # optional hand-specific event
        if "choice_keys" in df.columns:
            hand = normalise_key(row["choice_keys"])
            if hand is not None:
                base["trial_type"] = f"button_press_{hand}"
                new_rows.append(base.copy())

    n_press = sum(1 for r in new_rows if r["trial_type"] == "button_press")

    if dry_run:
        print(f"[DRY RUN] {src_path.name}: would add {n_press} button_press events ({n_skipped} rows skipped)")
        return n_press

    df_out = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    df_out = df_out.sort_values("onset").reset_index(drop=True)

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(dst_path, sep="\t", index=False, na_rep="n/a")

    print(f"[OK] {src_path.name}: added {n_press} button_press events ({n_skipped} rows skipped)")
    return n_press


def find_events_tsvs(bids_dir: Path, subjects: list, tasks: list) -> list:
    all_tsvs = sorted(bids_dir.rglob("*_events.tsv"))
    if not all_tsvs:
        raise SystemExit(f"No events TSVs found under: {bids_dir}")
    if subjects:
        sub_tags = {f"sub-{s}" for s in subjects}
        all_tsvs = [p for p in all_tsvs if any(t in p.parts for t in sub_tags)]
    if tasks:
        all_tsvs = [p for p in all_tsvs if any(f"task-{t}" in p.name for t in tasks)]
    return all_tsvs


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--src-bids-dir", type=Path, required=True,
                    help="Source BIDS directory.")
    ap.add_argument("--dst-events-dir", type=Path, required=True,
                    help="Output directory for events TSVs with button press rows added.")
    ap.add_argument("--subjects", type=str, nargs="*", default=None)
    ap.add_argument("--tasks", type=str, nargs="*", default=None)
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing files in dst-events-dir.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.src_bids_dir.exists():
        sys.exit(f"ERROR: not found: {args.src_bids_dir}")

    tsvs = find_events_tsvs(args.src_bids_dir, args.subjects or [], args.tasks or [])
    print(f"Found {len(tsvs)} events TSV(s).\n")

    n_ok = n_skip = n_err = 0
    for src_path in tsvs:
        dst_path = args.dst_events_dir / src_path.relative_to(args.src_bids_dir)
        if dst_path.exists() and not args.force and not args.dry_run:
            print(f"[SKIP] Already exists: {dst_path.name}")
            n_skip += 1
            continue
        try:
            process_tsv(src_path, dst_path, dry_run=args.dry_run)
            n_ok += 1
        except Exception as e:
            print(f"[ERROR] {src_path.name}: {e}", file=sys.stderr)
            n_err += 1

    print(f"\n== Done ==  OK: {n_ok}  Skipped: {n_skip}  Errors: {n_err}")
    if n_err:
        sys.exit(1)


if __name__ == "__main__":
    main()