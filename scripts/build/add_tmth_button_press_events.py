#!/usr/bin/env python3
"""
tmth_add_button_press_events.py
==========================
Creates a directory of events TSVs with button press delta events added,
derived from the choice_RTs and choice_keys columns in the BIDS events files.

RATIONALE
---------
The choice period (trial_type=choice) lasts ~4 seconds.  For a motor sanity
check we want the precise moment of each button press as a delta event
(duration=0), which produces a sharper sensorimotor response when convolved
with the HRF.

Participants may press multiple times per trial.  Each press gets its own row.

Three trial_type labels are added per press:
  button_press          -- hand-agnostic
  button_press_left     -- left hand only
  button_press_right    -- right hand only

This supports two motor models:
  Model 1: button_press > fixation                    (bilateral motor)
  Model 2: button_press_left > button_press_right     (lateralization)

BUTTON PRESS ONSET
------------------
  onset = scannerTimer_choice_Start + choice_RT_i

choice_RTs  : stringified list e.g. '[0.317, 0.684, 1.420]'  (relative to choice start)
choice_keys : stringified list e.g. "['left', 'left', 'right']"

USAGE
-----
  # Dry run first to verify:
  python3 add_button_press_events.py \\
    --src-bids-dir /path/to/slb_bids_runs \\
    --dst-events-dir /path/to/slb_events_motor \\
    --tasks tm th --dry-run

  # Apply:
  python3 add_button_press_events.py \\
    --src-bids-dir /path/to/slb_bids_runs \\
    --dst-events-dir /path/to/slb_events_motor \\
    --tasks tm th

  # Build motor BIDS thin-clone using the custom events:
  python3 rebuild_bids_runs_thinclone.py \\
    --src /path/to/slb_bids_runs \\
    --dst /path/to/slb_bids_motor \\
    --events-override-dir /path/to/slb_events_motor

  # Run motor pipeline (cfg points BIDS_DIR at slb_bids_motor):
  run_pipeline.sh --config tmth_motor_bilateral.cfg --steps all
  run_pipeline.sh --config tmth_motor_lateralization.cfg --steps all
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
    """Parse a stringified Python list e.g. '[0.317, 0.684]' or \"['left', 'right']\".
    Returns a list, or [] if value is NaN / unparseable.
    """
    if pd.isna(value):
        return []
    s = str(value).strip()
    if not s or s in ("nan", "[]", ""):
        return []
    try:
        result = ast.literal_eval(s)
        return result if isinstance(result, list) else [result]
    except Exception:
        return []


def normalise_key(key) -> str:
    """Return 'left', 'right', or None."""
    if key is None:
        return None
    k = str(key).strip().lower()
    if "left" in k:
        return "left"
    if "right" in k:
        return "right"
    return None


def process_tsv(src_path: Path, dst_path: Path, dry_run: bool) -> int:
    """Add button press rows to one events TSV. Returns number of presses added."""
    df = pd.read_csv(src_path, sep="\t")

    required = {"trial_type", "scannerTimer_choice_Start", "choice_RTs", "choice_keys"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError("Missing columns {}: {}".format(missing, src_path.name))

    choice_rows = df[df["trial_type"] == "choice"]
    if len(choice_rows) == 0:
        print("[WARN] No choice rows: {}".format(src_path.name))
        return 0

    new_rows = []
    n_skipped = 0

    for _, row in choice_rows.iterrows():
        rts  = parse_list_column(row["choice_RTs"])
        keys = parse_list_column(row["choice_keys"])
        t0   = float(row["scannerTimer_choice_Start"])

        if not rts:
            n_skipped += 1
            continue

        if len(keys) != len(rts):
            print("[WARN] {}: trial {} — {} RTs vs {} keys, padding with None".format(
                src_path.name, row.get("trial", "?"), len(rts), len(keys)))
        keys_padded = list(keys) + [None] * max(0, len(rts) - len(keys))

        for rt, key in zip(rts, keys_padded):
            onset = round(t0 + float(rt), 6)
            hand  = normalise_key(key)

            base = row.copy()
            base["onset"]    = onset
            base["duration"] = 0.0

            # Hand-agnostic row
            base["trial_type"] = "button_press"
            new_rows.append(base.copy())

            # Hand-specific row
            if hand is not None:
                base["trial_type"] = "button_press_{}".format(hand)
                new_rows.append(base.copy())

    n_press = sum(1 for r in new_rows if r["trial_type"] == "button_press")

    if dry_run:
        print("[DRY RUN] {} : would add {} button_press events ({} trials skipped)".format(
            src_path.name, n_press, n_skipped))
        return n_press

    df_out = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    df_out = df_out.sort_values("onset").reset_index(drop=True)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(dst_path, sep="\t", index=False, na_rep="n/a")
    print("[OK] {} : added {} button_press events ({} trials skipped)".format(
        src_path.name, n_press, n_skipped))
    return n_press


def find_events_tsvs(bids_dir: Path, subjects: list, tasks: list) -> list:
    all_tsvs = sorted(bids_dir.rglob("*_events.tsv"))
    if not all_tsvs:
        raise SystemExit("No events TSVs found under: {}".format(bids_dir))
    if subjects:
        sub_tags = {"sub-{}".format(s) for s in subjects}
        all_tsvs = [p for p in all_tsvs if any(t in p.parts for t in sub_tags)]
    if tasks:
        all_tsvs = [p for p in all_tsvs
                    if any("task-{}".format(t) in p.name for t in tasks)]
    return all_tsvs


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--src-bids-dir",   type=Path, required=True,
        help="Source BIDS directory (original, unmodified).")
    ap.add_argument("--dst-events-dir", type=Path, required=True,
        help="Output directory for events TSVs with button press rows added.")
    ap.add_argument("--subjects", type=str, nargs="*", default=None)
    ap.add_argument("--tasks",    type=str, nargs="*", default=None)
    ap.add_argument("--force",    action="store_true",
        help="Overwrite existing files in dst-events-dir.")
    ap.add_argument("--dry-run",  action="store_true")
    args = ap.parse_args()

    if not args.src_bids_dir.exists():
        sys.exit("ERROR: not found: {}".format(args.src_bids_dir))

    tsvs = find_events_tsvs(args.src_bids_dir, args.subjects or [], args.tasks or [])
    print("Found {} events TSV(s).\n".format(len(tsvs)))

    n_ok = n_skip = n_err = 0
    for src_path in tsvs:
        dst_path = args.dst_events_dir / src_path.relative_to(args.src_bids_dir)
        if dst_path.exists() and not args.force and not args.dry_run:
            print("[SKIP] Already exists: {}".format(dst_path.name))
            n_skip += 1
            continue
        try:
            process_tsv(src_path, dst_path, dry_run=args.dry_run)
            n_ok += 1
        except Exception as e:
            print("[ERROR] {}: {}".format(src_path.name, e), file=sys.stderr)
            n_err += 1

    print("\n== Done ==  OK: {}  Skipped: {}  Errors: {}".format(n_ok, n_skip, n_err))
    if n_err:
        sys.exit(1)

    if not args.dry_run and n_ok > 0:
        print("""
== Next steps ==
1. Build motor BIDS thin-clone:
     python3 rebuild_bids_runs_thinclone.py \\
       --src  {src} \\
       --dst  {src}_motor \\
       --events-override-dir {dst}

2. Run motor pipelines:
     run_pipeline.sh --config tmth_motor_bilateral.cfg --steps all
     run_pipeline.sh --config tmth_motor_lateralization.cfg --steps all
""".format(src=args.src_bids_dir, dst=args.dst_events_dir))


if __name__ == "__main__":
    main()