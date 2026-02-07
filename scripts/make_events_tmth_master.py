#!/usr/bin/env python3
import sys
from pathlib import Path

import pandas as pd


# ---------- Paths ----------
BEHAV_ROOT = Path("/data/sld/homes/collab/slb/logs/fMRI/data/preprocessed/individual")
BIDS_ROOT = Path("/data/sld/homes/vguigon/work/slb_bids")

# Data dictionary (server-stable path)
DATA_DICT = Path("/data/sld/homes/vguigon/work/docs/Trust-DataDictionaryfMRI.csv")

SUBJECTS = ["000", "001", "002", "003"]

# (condition dir, run dir, BIDS task label)
TASK_RUNS = [
    ("tm", "run-01", "tm1"),
    ("tm", "run-02", "tm2"),
    ("th", "run-01", "th1"),
    ("th", "run-02", "th2"),
]

EXPECTED_N_TRIALS = 40


# ---------- Event definitions (TM/TH) ----------
COL_FIX = "scannerTimer_fixation1_Start"
COL_CHOICE = "scannerTimer_choice_Start"
COL_WAIT = "scannerTimer_wait_Start"
COL_FB = "scannerTimer_feedback_Start"
COL_END = "scannerTimer_trial_End"

TIMING_COLS = [COL_FIX, COL_CHOICE, COL_WAIT, COL_FB, COL_END]


def load_required_vars(data_dict_path: Path):
    dd = pd.read_csv(data_dict_path)
    req = dd.loc[dd["Required"].astype(str).str.strip().str.lower() == "yes", "Variable"]
    return [str(x) for x in req.tolist()]


def find_unique_csv(subj: str, cond: str, run: str) -> Path:
    run_dir = BEHAV_ROOT / cond / ("SLB_%s" % subj) / run
    hits = sorted(run_dir.glob("*_preprocessed.csv"))
    if len(hits) != 1:
        raise RuntimeError("Expected exactly 1 CSV in %s, found %d: %s" % (run_dir, len(hits), hits))
    return hits[0]


def coerce_numeric(df: pd.DataFrame, cols, label: str) -> pd.DataFrame:
    for c in cols:
        if c not in df.columns:
            raise RuntimeError("%s: missing required timing column: %s" % (label, c))
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def assert_no_missing_timestamps(trials: pd.DataFrame, label: str) -> None:
    """
    Fail if any trial row has NaN in any required timing anchor after numeric coercion.
    This is the 'missing timestamps' check.
    """
    missing_mask = trials[TIMING_COLS].isna().any(axis=1)
    if missing_mask.any():
        bad_idx = missing_mask[missing_mask].index.tolist()[:10]
        bad = trials.loc[bad_idx, TIMING_COLS]
        raise RuntimeError(
            "%s: missing timestamp(s) in required timing columns for trial-row indices %s.\n%s"
            % (label, bad_idx, bad)
        )


def build_master_events(df: pd.DataFrame, required_vars, label: str) -> pd.DataFrame:
    # trial rows = rows with a fixation onset
    if COL_FIX not in df.columns:
        raise RuntimeError("%s: missing column %s" % (label, COL_FIX))

    trials = df.loc[~df[COL_FIX].isna(), :].copy()

    if len(trials) != EXPECTED_N_TRIALS:
        raise RuntimeError(
            "%s: expected %d trials, found %d (rows with %s)"
            % (label, EXPECTED_N_TRIALS, len(trials), COL_FIX)
        )

    # ensure numeric timing columns
    trials = coerce_numeric(trials, TIMING_COLS, label)

    # Missing timestamps check (NaN in any timing anchor)
    assert_no_missing_timestamps(trials, label)

    # sort by fixation onset (stable)
    trials = trials.sort_values(COL_FIX, kind="mergesort").reset_index(drop=True)

    # durations for each event
    dur_fix = trials[COL_CHOICE] - trials[COL_FIX]
    dur_choice = trials[COL_WAIT] - trials[COL_CHOICE]     # CHOICE AS EPOCH
    dur_wait = trials[COL_FB] - trials[COL_WAIT]
    dur_fb = trials[COL_END] - trials[COL_FB]

    # sanity checks: durations must be strictly positive for epochs
    if (dur_fix <= 0).any():
        bad = dur_fix[dur_fix <= 0].index.tolist()[:10]
        raise RuntimeError("%s: non-positive fixation durations at indices: %s" % (label, bad))
    if (dur_choice <= 0).any():
        bad = dur_choice[dur_choice <= 0].index.tolist()[:10]
        raise RuntimeError("%s: non-positive choice durations at indices: %s" % (label, bad))
    if (dur_wait <= 0).any():
        bad = dur_wait[dur_wait <= 0].index.tolist()[:10]
        raise RuntimeError("%s: non-positive wait durations at indices: %s" % (label, bad))
    if (dur_fb <= 0).any():
        bad = dur_fb[dur_fb <= 0].index.tolist()[:10]
        raise RuntimeError("%s: non-positive feedback durations at indices: %s" % (label, bad))

    # choose which required vars we can actually carry (intersection with df columns)
    carry = [c for c in required_vars if c in trials.columns]

    rows = []
    for i in range(len(trials)):
        base = {"trial": int(i)}
        for c in carry:
            base[c] = trials.iloc[i][c]

        # fixation epoch
        r = dict(base)
        r["onset"] = float(trials.iloc[i][COL_FIX])
        r["duration"] = float(dur_fix.iloc[i])
        r["trial_type"] = "fixation"
        rows.append(r)

        # choice epoch (choice_start -> wait_start)
        r = dict(base)
        r["onset"] = float(trials.iloc[i][COL_CHOICE])
        r["duration"] = float(dur_choice.iloc[i])
        r["trial_type"] = "choice"
        rows.append(r)

        # wait epoch (wait_start -> feedback_start)
        r = dict(base)
        r["onset"] = float(trials.iloc[i][COL_WAIT])
        r["duration"] = float(dur_wait.iloc[i])
        r["trial_type"] = "wait"
        rows.append(r)

        # feedback epoch (feedback_start -> trial_end)
        r = dict(base)
        r["onset"] = float(trials.iloc[i][COL_FB])
        r["duration"] = float(dur_fb.iloc[i])
        r["trial_type"] = "feedback"
        rows.append(r)

    out = pd.DataFrame(rows).sort_values("onset", kind="mergesort").reset_index(drop=True)

    # minimal BIDS-required first
    first_cols = ["onset", "duration", "trial_type"]
    extra_cols = [c for c in out.columns if c not in first_cols]
    out = out.loc[:, first_cols + extra_cols]

    # BIDS sanity
    if not out["onset"].is_monotonic_increasing:
        raise RuntimeError("%s: output onsets not monotonic increasing" % label)
    if (out["duration"] < 0).any():
        raise RuntimeError("%s: negative durations in output" % label)

    return out


def out_path(subj: str, task: str) -> Path:
    return (
        BIDS_ROOT
        / ("sub-%s" % subj)
        / "func"
        / ("sub-%s_task-%s_events.tsv" % (subj, task))
    )


def main():
    if not DATA_DICT.exists():
        raise RuntimeError("Data dictionary not found at %s" % DATA_DICT)

    required_vars = load_required_vars(DATA_DICT)

    for subj in SUBJECTS:
        for cond, run, task in TASK_RUNS:
            csv_path = find_unique_csv(subj, cond, run)
            df = pd.read_csv(csv_path)

            label = "sub-%s task-%s (%s)" % (subj, task, csv_path.name)
            events = build_master_events(df, required_vars, label)

            out_tsv = out_path(subj, task)
            out_tsv.parent.mkdir(parents=True, exist_ok=True)
            events.to_csv(out_tsv, sep="\t", index=False, float_format="%.3f")

            # Expect 4 event rows per trial
            expected_rows = EXPECTED_N_TRIALS * 4
            if len(events) != expected_rows:
                raise RuntimeError("%s: expected %d event rows, got %d" % (label, expected_rows, len(events)))

            print("OK:", out_tsv)


if __name__ == "__main__":
    sys.exit(main())
