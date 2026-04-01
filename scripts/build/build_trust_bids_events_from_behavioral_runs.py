#!/usr/bin/env python3
# build_trust_bids_events.py
#
# Converts preprocessed Trust (TM/TH) behavioral CSVs into BIDS-compliant
# events.tsv files.
#
# Event types produced per trial:
#   new_partner     - partner introduction screen (once per partner block, i.e. twice per run)
#   fixation        - inter-trial fixation cross
#   choice_success  - decision screen, participant responded
#   choice_miss     - decision screen, participant did not respond (responded == 0)
#   wait_success    - waiting screen after a valid response
#   wait_miss       - waiting screen on missed trials (computer-selected amount shown)
#   feedback        - outcome screen (identical structure regardless of response)
#
# All durations are derived from consecutive scannerTimer values.
# Carry-through columns are driven by 'Separate column' entries in the
# Trust data dictionary ('Keep in .tsv file' column).

import sys
from pathlib import Path
import pandas as pd


# ---------- Paths ----------
BEHAV_ROOT = Path("/data/sld/homes/collab/slb/behav_data/fMRI/data")
BIDS_ROOT = Path("/data/sld/homes/vguigon/slb_work/slb_bids_runs")

# ---------- Data dictionary ----------
TRUST_DICT = Path("/data/sld/homes/vguigon/slb_work/docs/dictionaries/Trust-DataDictionaryfMRI.csv")

TASK_RUNS = [
    # (condition folder, run folder name, BIDS task label, BIDS run label)
    ("tm", "run-01", "tm", "01"),
    ("tm", "run-02", "tm", "02"),
    ("th", "run-01", "th", "01"),
    ("th", "run-02", "th", "02"),
]


# ---------- Dictionary helpers ----------
def load_separate_cols(data_dict_path):
    """
    Return variables marked 'Separate column' in 'Keep in .tsv file'.
    These are carried through as extra columns alongside onset/duration/trial_type.
    """
    dd = pd.read_csv(data_dict_path)
    mask = dd["Keep in .tsv file"].astype(str).str.strip() == "Separate column"
    return [str(x) for x in dd.loc[mask, "Variable"].tolist()]


# ---------- File discovery ----------
def discover_subjects_from_behav_root():
    subjects = []
    for p in sorted(BEHAV_ROOT.glob("SLB_*")):
        if p.is_dir():
            subj = p.name.replace("SLB_", "")
            if subj:
                subjects.append(subj)
    return sorted(subjects)


def _candidate_cond_dirs(subj, cond):
    subj_dir = BEHAV_ROOT / ("SLB_%s" % subj)
    dirs = [
        subj_dir / cond,
        subj_dir / cond.lower(),
        subj_dir / cond.upper(),
        subj_dir / cond.capitalize(),
    ]
    out, seen = [], set()
    for d in dirs:
        if d not in seen:
            out.append(d)
            seen.add(d)
    return out


def _pick_one_csv(hits, label):
    if len(hits) == 1:
        return hits[0]
    pref = [p for p in hits if "preprocessed" in p.name.lower()]
    if len(pref) == 1:
        return pref[0]
    if len(pref) > 1:
        hits = pref
    hits = sorted(hits, key=lambda p: p.stat().st_mtime, reverse=True)
    print("[WARN] %s: multiple CSVs found; using newest: %s" % (label, hits[0].name))
    return hits[0]


def find_csv(subj, cond, run_dir):
    """
    Preferred:  .../SLB_<subj>/<cond>/preprocessed/<run_dir>/*.csv
    Fallbacks:  .../SLB_<subj>/<cond>/preprocessed/*.csv
                .../SLB_<subj>/<cond>/*.csv
    """
    cond_path = None
    for d in _candidate_cond_dirs(subj, cond):
        if d.exists():
            cond_path = d
            break
    if cond_path is None:
        raise RuntimeError("Condition dir not found for sub=%s cond=%s" % (subj, cond))

    pre_dir = cond_path / "preprocessed"
    label = "sub-%s cond=%s run=%s" % (subj, cond, run_dir)

    run_path = pre_dir / run_dir
    if run_path.exists():
        hits = sorted(run_path.glob("*.csv"))
        if hits:
            return _pick_one_csv(hits, label)

    if pre_dir.exists():
        hits = sorted(pre_dir.glob("*.csv"))
        if hits:
            return _pick_one_csv(hits, label)

    hits = sorted(cond_path.glob("*.csv"))
    if hits:
        return _pick_one_csv(hits, label)

    raise RuntimeError(
        "No .csv found in %s (checked %s, %s, %s)" % (label, run_path, pre_dir, cond_path)
    )


# ---------- Shared utilities ----------
def coerce_numeric(df, cols, label):
    for c in cols:
        if c not in df.columns:
            raise RuntimeError("%s: missing required timing column: %s" % (label, c))
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def finalize_events_df(out, label):
    first_cols = ["onset", "duration", "trial_type"]
    extra_cols = [c for c in out.columns if c not in first_cols]
    out = out.loc[:, first_cols + extra_cols]

    if not out["onset"].is_monotonic_increasing:
        raise RuntimeError("%s: output onsets not monotonic increasing" % label)
    if (out["duration"] < 0).any():
        raise RuntimeError("%s: negative durations in output" % label)

    return out


def out_path(subj, task, run_label):
    func_dir = BIDS_ROOT / ("sub-%s" % subj) / "func"
    fname = "sub-%s_task-%s_run-%s_events.tsv" % (subj, task, run_label)
    return func_dir / fname


# ---------- Trust (tm / th) ----------
TR_COL_COND   = "scannerTimer_condition_Start"
TR_COL_FIX    = "scannerTimer_fixation1_Start"
TR_COL_CHOICE = "scannerTimer_choice_Start"
TR_COL_WAIT   = "scannerTimer_wait_Start"
TR_COL_FB     = "scannerTimer_feedback_Start"
TR_COL_END    = "scannerTimer_trial_End"

TR_TIMING_COLS = [TR_COL_COND, TR_COL_FIX, TR_COL_CHOICE, TR_COL_WAIT, TR_COL_FB, TR_COL_END]

TR_RESPONDED     = "responded"
TR_PARTNER_TRIAL = "partnerTrialNumber"


def build_trust_events(df, separate_cols, label):
    # ---- Identify trial rows ----
    if TR_COL_FIX not in df.columns:
        raise RuntimeError("%s: missing column %s" % (label, TR_COL_FIX))

    trials = df.loc[~df[TR_COL_FIX].isna(), :].copy()
    if len(trials) == 0:
        raise RuntimeError("%s: found 0 trials (no non-null rows in %s)" % (label, TR_COL_FIX))

    trials = coerce_numeric(trials, TR_TIMING_COLS, label)

    missing_mask = trials[TR_TIMING_COLS].isna().any(axis=1)
    if missing_mask.any():
        bad_idx = missing_mask[missing_mask].index.tolist()[:10]
        bad = trials.loc[bad_idx, TR_TIMING_COLS]
        raise RuntimeError(
            "%s: missing timestamp(s) in required timing columns at row indices %s.\n%s"
            % (label, bad_idx, bad)
        )

    for col in [TR_RESPONDED, TR_PARTNER_TRIAL]:
        if col not in trials.columns:
            raise RuntimeError("%s: missing required column %s" % (label, col))
    trials[TR_RESPONDED]     = pd.to_numeric(trials[TR_RESPONDED],     errors="coerce")
    trials[TR_PARTNER_TRIAL] = pd.to_numeric(trials[TR_PARTNER_TRIAL], errors="coerce")

    bad_responded = trials[~trials[TR_RESPONDED].isin([0, 1])]
    if len(bad_responded) > 0:
        raise RuntimeError(
            "%s: unexpected values in '%s' (expected 0 or 1):\n%s"
            % (label, TR_RESPONDED, bad_responded[[TR_PARTNER_TRIAL, TR_RESPONDED]].head(10))
        )

    trials = trials.sort_values(TR_COL_FIX, kind="mergesort").reset_index(drop=True)

    # ---- Derive durations from consecutive timer values ----
    dur_new_partner = trials[TR_COL_FIX]    - trials[TR_COL_COND]
    dur_fix         = trials[TR_COL_CHOICE] - trials[TR_COL_FIX]
    dur_choice      = trials[TR_COL_WAIT]   - trials[TR_COL_CHOICE]
    dur_wait        = trials[TR_COL_FB]     - trials[TR_COL_WAIT]
    dur_fb          = trials[TR_COL_END]    - trials[TR_COL_FB]

    intro_mask = trials[TR_PARTNER_TRIAL] == 1
    for name, dur, mask in [
        ("fixation",    dur_fix,         None),
        ("choice",      dur_choice,      None),
        ("wait",        dur_wait,        None),
        ("feedback",    dur_fb,          None),
        ("new_partner", dur_new_partner, intro_mask),
    ]:
        check = dur if mask is None else dur[mask]
        if (check <= 0).any():
            bad = check[check <= 0].index.tolist()[:10]
            raise RuntimeError(
                "%s: non-positive %s duration(s) at trial indices: %s" % (label, name, bad)
            )

    # ---- Carry columns: only those actually present in the data ----
    carry = [c for c in separate_cols if c in trials.columns]

    # ---- Build event rows ----
    rows = []
    for i in range(len(trials)):
        t = trials.iloc[i]
        base = {c: t[c] for c in carry}
        responded = int(t[TR_RESPONDED])

        # new_partner: only on first trial of each partner block
        if t[TR_PARTNER_TRIAL] == 1:
            rows.append(dict(base,
                onset=float(t[TR_COL_COND]),
                duration=float(dur_new_partner.iloc[i]),
                trial_type="new_partner",
            ))

        # fixation: identical regardless of response
        rows.append(dict(base,
            onset=float(t[TR_COL_FIX]),
            duration=float(dur_fix.iloc[i]),
            trial_type="fixation",
        ))

        # choice and wait: split by response status
        # On missed trials the wait screen still appears but shows a
        # computer-selected amount rather than the participant's decision outcome.
        rows.append(dict(base,
            onset=float(t[TR_COL_CHOICE]),
            duration=float(dur_choice.iloc[i]),
            trial_type="choice_success" if responded == 1 else "choice_miss",
        ))
        rows.append(dict(base,
            onset=float(t[TR_COL_WAIT]),
            duration=float(dur_wait.iloc[i]),
            trial_type="wait_success" if responded == 1 else "wait_miss",
        ))

        # feedback: identical regardless of response
        rows.append(dict(base,
            onset=float(t[TR_COL_FB]),
            duration=float(dur_fb.iloc[i]),
            trial_type="feedback",
        ))

    out = pd.DataFrame(rows).sort_values("onset", kind="mergesort").reset_index(drop=True)
    out = finalize_events_df(out, label)

    n_intros = int(intro_mask.sum())
    expected_rows = len(trials) * 4 + n_intros
    if len(out) != expected_rows:
        raise RuntimeError(
            "%s: row count mismatch (expected %d = %d trials x 4 + %d new_partner rows, got %d)"
            % (label, expected_rows, len(trials), n_intros, len(out))
        )

    return out


# ---------- Main ----------
def main():
    if not TRUST_DICT.exists():
        raise RuntimeError("Data dictionary not found at %s" % TRUST_DICT)

    separate_cols = load_separate_cols(TRUST_DICT)

    subjects = discover_subjects_from_behav_root()
    if not subjects:
        raise RuntimeError(
            "No subjects found under %s (expected folders like SLB_000)." % BEHAV_ROOT
        )

    for subj in subjects:
        subj_dir = BEHAV_ROOT / ("SLB_%s" % subj)
        if not subj_dir.exists():
            continue

        for cond, run_dir, task, run_label in TASK_RUNS:
            try:
                csv_path = find_csv(subj, cond, run_dir)
            except RuntimeError as e:
                print("[SKIP]", e)
                continue

            df = pd.read_csv(csv_path)
            label = "sub-%s task-%s run-%s (%s)" % (subj, task, run_label, csv_path.name)

            events = build_trust_events(df, separate_cols, label)

            tsv = out_path(subj, task, run_label)
            tsv.parent.mkdir(parents=True, exist_ok=True)
            events.to_csv(tsv, sep="\t", index=False, float_format="%.3f")

            print("OK:", tsv)

    return 0


if __name__ == "__main__":
    sys.exit(main())
