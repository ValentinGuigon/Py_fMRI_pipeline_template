#!/usr/bin/env python3
# build_ol_bids_events.py
#
# Converts preprocessed OL behavioral CSVs into BIDS-compliant events.tsv files.
#
# Logic is dictionary-driven:
#   - "Pivot to onset" rows define onset columns
#   - matching "Pivot to duration" rows define duration columns
#   - "trial_type label in .tsv file" defines BIDS trial_type
#   - "Separate column" rows are copied into every output event row
#
# Important:
#   - Pivot columns are used internally to build onset/duration/trial_type
#     but are NOT preserved in the final events.tsv.
#   - trialNb and trType are always preserved.
#   - Event emission is restricted by trType:
#       observe_*    -> trType == 1
#       play_*       -> trType == 2
#       iti_fixation -> allowed in both
#
# Example output event types (base TSV, before any GLM-specific enrichment)
#
# Observe trials (trType == 1):
#   observe_start      -> initial observe fixation period
#   observe_stimulus   -> slot machine display
#   observe_wait       -> waiting period before partner outcome/video
#   observe_video      -> partner choice / play video period
#   iti_fixation       -> inter-trial fixation
#
# Play trials (trType == 2), responded == 1:
#   play_start         -> initial play fixation period
#   play_choice        -> participant choice period
#   play_validation    -> feedback / validation screen
#   play_wait          -> waiting period before token display
#   play_token         -> token outcome period
#   iti_fixation       -> inter-trial fixation
#
# Play trials (trType == 2), responded == 0 (missed):
#   play_start         -> initial play fixation period
#   play_choice        -> participant choice period (no response recorded)
#   miss               -> collapsed event spanning validation + wait_token + token
#                         (onset = scannerTimer_feedback_Start,
#                          duration = feedback_dur + wait_token_dur + token_dur)
#   iti_fixation       -> inter-trial fixation
#
# Notes:
#   - On missed trials a "Missed Response!" screen obscures the validation,
#     wait_token, and token routines. Their timing is still saved in the CSV
#     but the stimuli are not shown, so those three events are suppressed and
#     replaced by a single 'miss' event covering the same interval.
#   - These are canonical/base task events only.
#   - No GLM enrichment is performed here.
#   - Later enrichment can derive model-specific regressors such as:
#       ObserveSlotMachine, PartnersChoice, SelfChoice, ButtonPress,
#       Token, FixationCross

import ast
import re
import sys
from pathlib import Path

import pandas as pd


# ---------- Paths ----------
BEHAV_ROOT = Path("/data/sld/homes/collab/slb/behav_data/fMRI/data")
BIDS_ROOT = Path("/data/sld/homes/vguigon/slb_work/slb_bids_runs")
OL_DICT = Path("/data/sld/homes/vguigon/slb_work/docs/dictionaries/OL-DataDictionaryfMRI.csv")

# ---------- OL runs ----------
TASK_RUNS = [
    # (condition folder, run folder name, BIDS task label, BIDS run label)
    ("ol", "run-01", "obslearn", "01"),
    ("ol", "run-02", "obslearn", "02"),
]


# ---------- Missed-trial constants ----------
MISS_SUPPRESSED = {"play_validation", "play_wait", "play_token"}
MISS_ONSET_COL = "scannerTimer_feedback_Start"
MISS_DUR_COLS = ["feedback_dur", "wait_token_dur", "token_dur"]


# ---------- Cleaning helpers ----------
def clean_scalar(x):
    """
    Convert list-like strings such as "[1.075]" into scalar 1.075.
    Leave normal scalars unchanged.
    """
    if pd.isna(x):
        return x

    if isinstance(x, list):
        if len(x) == 0:
            return pd.NA
        if len(x) == 1:
            return x[0]
        return str(x)

    if isinstance(x, str):
        s = x.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = ast.literal_eval(s)
                if isinstance(parsed, list):
                    if len(parsed) == 0:
                        return pd.NA
                    if len(parsed) == 1:
                        return parsed[0]
                    return str(parsed)
            except Exception:
                return x

    return x


def clean_rt_columns(df):
    """
    Normalize RT columns so single-value list strings become numeric scalars.
    """
    for col in df.columns:
        if col.endswith("_RT") or col == "choice_RT":
            df[col] = df[col].apply(clean_scalar)
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ---------- Dictionary helpers ----------
def load_ol_dictionary(data_dict_path):
    """
    Returns:
      carry_cols: list[str]
        Only columns marked 'Separate column' in the dictionary.
        trialNb and trType are force-included later if present.

      event_specs: list[dict], each with:
          onset_col, duration_col, trial_type

      pivot_cols: list[str]
        All pivot onset/duration columns used internally to build events.
    """
    dd = pd.read_csv(data_dict_path)

    required_cols = [
        "Variable",
        "Keep in .tsv file",
        "trial_type label in .tsv file",
    ]
    for c in required_cols:
        if c not in dd.columns:
            raise RuntimeError(f"Dictionary missing required column: {c}")

    var = dd["Variable"].astype(str).str.strip()
    keep = dd["Keep in .tsv file"].astype(str).str.strip()

    carry_cols = var[keep == "Separate column"].tolist()

    onset_rows = dd.loc[keep == "Pivot to onset", :].copy()
    dur_rows = dd.loc[keep == "Pivot to duration", :].copy()

    if len(onset_rows) == 0:
        raise RuntimeError("No 'Pivot to onset' rows found in OL dictionary")
    if len(dur_rows) == 0:
        raise RuntimeError("No 'Pivot to duration' rows found in OL dictionary")

    dur_map = {}
    for _, row in dur_rows.iterrows():
        trial_type = str(row["trial_type label in .tsv file"]).strip()
        duration_col = str(row["Variable"]).strip()
        dur_map.setdefault(trial_type, []).append(duration_col)

    event_specs = []
    dur_use_count = {}

    for _, row in onset_rows.iterrows():
        onset_col = str(row["Variable"]).strip()
        trial_type = str(row["trial_type label in .tsv file"]).strip()

        if not trial_type or trial_type.lower() == "nan":
            raise RuntimeError(
                f"Onset column {onset_col} has no trial_type label in dictionary"
            )

        candidates = dur_map.get(trial_type, [])
        if len(candidates) == 0:
            raise RuntimeError(
                f"No matching duration row found for trial_type '{trial_type}' "
                f"(onset column: {onset_col})"
            )

        idx = dur_use_count.get(trial_type, 0)
        if idx >= len(candidates):
            raise RuntimeError(
                f"More onset rows than duration rows for trial_type '{trial_type}'"
            )

        duration_col = candidates[idx]
        dur_use_count[trial_type] = idx + 1

        event_specs.append(
            {
                "onset_col": onset_col,
                "duration_col": duration_col,
                "trial_type": trial_type,
            }
        )

    pivot_cols = sorted(
        set(
            [spec["onset_col"] for spec in event_specs]
            + [spec["duration_col"] for spec in event_specs]
        )
    )

    # Safety: do not carry pivot columns into final TSV even if dictionary is messy
    carry_cols = [c for c in carry_cols if c not in pivot_cols]

    return carry_cols, event_specs, pivot_cols


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
    subj_dir = BEHAV_ROOT / f"SLB_{subj}"
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
    print(f"[WARN] {label}: multiple CSVs found; using newest: {hits[0].name}")
    return hits[0]


def _run_number_from_name(run_dir):
    m = re.search(r"run-(\d+)", run_dir)
    if not m:
        raise RuntimeError(f"Could not parse run number from {run_dir}")
    return int(m.group(1))


def find_csv(subj, cond, run_dir):
    """
    Preferred:
      .../SLB_<subj>/<cond>/preprocessed/<run_dir>/*.csv

    Fallbacks:
      .../SLB_<subj>/<cond>/preprocessed/*run*<n>*.csv
      .../SLB_<subj>/<cond>/preprocessed/*.csv
      .../SLB_<subj>/<cond>/*.csv
    """
    cond_path = None
    for d in _candidate_cond_dirs(subj, cond):
        if d.exists():
            cond_path = d
            break

    if cond_path is None:
        raise RuntimeError(f"Condition dir not found for sub={subj} cond={cond}")

    pre_dir = cond_path / "preprocessed"
    label = f"sub-{subj} cond={cond} run={run_dir}"
    run_n = _run_number_from_name(run_dir)

    run_path = pre_dir / run_dir
    if run_path.exists():
        hits = sorted(run_path.glob("*.csv"))
        if hits:
            return _pick_one_csv(hits, label)

    if pre_dir.exists():
        pattern_hits = sorted(pre_dir.glob(f"*run*{run_n}*.csv"))
        if pattern_hits:
            return _pick_one_csv(pattern_hits, label)

        hits = sorted(pre_dir.glob("*.csv"))
        if hits:
            return _pick_one_csv(hits, label)

    hits = sorted(cond_path.glob("*.csv"))
    if hits:
        return _pick_one_csv(hits, label)

    raise RuntimeError(
        f"No .csv found in {label} (checked {run_path}, {pre_dir}, {cond_path})"
    )


# ---------- Shared utilities ----------
def coerce_numeric(df, cols, label):
    for c in cols:
        if c not in df.columns:
            raise RuntimeError(f"{label}: missing required column: {c}")
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def finalize_events_df(out, label):
    first_cols = ["onset", "duration", "trial_type"]
    extra_cols = [c for c in out.columns if c not in first_cols]
    out = out.loc[:, first_cols + extra_cols]

    if not out["onset"].is_monotonic_increasing:
        raise RuntimeError(f"{label}: output onsets not monotonic increasing")

    if (out["duration"] < 0).any():
        bad = out.index[out["duration"] < 0].tolist()[:10]
        raise RuntimeError(f"{label}: negative durations at output rows {bad}")

    return out


def out_path(subj, task, run_label):
    func_dir = BIDS_ROOT / f"sub-{subj}" / "func"
    fname = f"sub-{subj}_task-{task}_run-{run_label}_events.tsv"
    return func_dir / fname


# ---------- OL builder ----------
def label_allowed_for_trtype(trial_type, tr_type):
    if trial_type.startswith("observe_"):
        return tr_type == 1
    if trial_type.startswith("play_"):
        return tr_type == 2
    if trial_type == "iti_fixation":
        return tr_type in (1, 2)
    return True


def build_ol_events(df, carry_cols, event_specs, label):
    df = clean_rt_columns(df)

    # Force-keep these identifiers even if dictionary misses them
    for forced in ["trialNb", "trType"]:
        if forced in df.columns and forced not in carry_cols:
            carry_cols.append(forced)

    needed_cols = []
    for spec in event_specs:
        needed_cols.extend([spec["onset_col"], spec["duration_col"]])

    needed_cols.extend(["trialNb", "trType", "responded"])
    needed_cols.extend(MISS_DUR_COLS + [MISS_ONSET_COL])
    needed_cols = sorted(set(needed_cols))

    df = coerce_numeric(df, needed_cols, label)

    onset_cols = [spec["onset_col"] for spec in event_specs]
    trial_mask = df[onset_cols].notna().any(axis=1)
    trials = df.loc[trial_mask, :].copy()

    if len(trials) == 0:
        raise RuntimeError(f"{label}: found 0 trials (no non-null onset rows)")

    bad_trtype = trials.loc[~trials["trType"].isin([1, 2]), ["trialNb", "trType"]].head(10)
    if len(bad_trtype) > 0:
        raise RuntimeError(f"{label}: unexpected trType values:\n{bad_trtype}")

    play_trials = trials.loc[trials["trType"] == 2]
    bad_resp = play_trials.loc[
        ~play_trials["responded"].isin([0, 1]), ["trialNb", "responded"]
    ].head(10)
    if len(bad_resp) > 0:
        raise RuntimeError(
            f"{label}: unexpected 'responded' values on play trials:\n{bad_resp}"
        )

    trials["_sort_onset"] = trials[onset_cols].min(axis=1, skipna=True)
    trials = trials.sort_values("_sort_onset", kind="mergesort").reset_index(drop=True)
    trials = trials.drop(columns=["_sort_onset"])

    carry = [c for c in carry_cols if c in trials.columns]

    rows = []
    for i in range(len(trials)):
        t = trials.iloc[i]
        tr_type = int(t["trType"])

        base = {c: t[c] for c in carry}
        base["trialNb"] = int(t["trialNb"])
        base["trType"] = tr_type

        is_miss = (tr_type == 2) and (int(t["responded"]) == 0)

        emitted_this_row = 0

        for spec in event_specs:
            onset_col = spec["onset_col"]
            duration_col = spec["duration_col"]
            trial_type = spec["trial_type"]

            if not label_allowed_for_trtype(trial_type, tr_type):
                continue

            if is_miss and trial_type in MISS_SUPPRESSED:
                continue

            onset = t[onset_col]
            duration = t[duration_col]

            if pd.isna(onset) or pd.isna(duration):
                continue

            if duration <= 0:
                raise RuntimeError(
                    f"{label}: non-positive duration for trial_type '{trial_type}' "
                    f"at trial row {i} ({duration_col}={duration})"
                )

            rows.append(
                dict(
                    base,
                    onset=float(onset),
                    duration=float(duration),
                    trial_type=trial_type,
                )
            )
            emitted_this_row += 1

        if is_miss:
            miss_onset = t[MISS_ONSET_COL]

            if pd.isna(miss_onset):
                raise RuntimeError(
                    f"{label}: missing {MISS_ONSET_COL} on missed trial row {i} "
                    f"(trialNb={base['trialNb']})"
                )

            if any(pd.isna(t[c]) for c in MISS_DUR_COLS):
                raise RuntimeError(
                    f"{label}: missing duration component for 'miss' event at trial row {i} "
                    f"(trialNb={base['trialNb']})"
                )

            miss_dur = sum(float(t[c]) for c in MISS_DUR_COLS)
            if miss_dur <= 0:
                raise RuntimeError(
                    f"{label}: non-positive 'miss' duration at trial row {i} "
                    f"(trialNb={base['trialNb']})"
                )

            rows.append(
                dict(
                    base,
                    onset=float(miss_onset),
                    duration=float(miss_dur),
                    trial_type="miss",
                )
            )
            emitted_this_row += 1

        if emitted_this_row == 0:
            raise RuntimeError(
                f"{label}: no valid events emitted for trial row {i} "
                f"(trialNb={base['trialNb']}, trType={base['trType']})"
            )

    if len(rows) == 0:
        raise RuntimeError(f"{label}: no event rows were produced")

    out = pd.DataFrame(rows).sort_values("onset", kind="mergesort").reset_index(drop=True)
    out = finalize_events_df(out, label)
    return out


# ---------- Main ----------
def main():
    if not OL_DICT.exists():
        raise RuntimeError(f"OL data dictionary not found at {OL_DICT}")

    carry_cols, event_specs, pivot_cols = load_ol_dictionary(OL_DICT)

    print("[INFO] Pivot columns used internally but excluded from final TSV:")
    print("       " + ", ".join(pivot_cols))
    print("[INFO] Carried-through columns:")
    print("       " + ", ".join(carry_cols))
    print("[INFO] Event specs:")
    for spec in event_specs:
        print(
            f"       {spec['onset_col']} + {spec['duration_col']} -> {spec['trial_type']}"
        )

    subjects = discover_subjects_from_behav_root()
    if not subjects:
        raise RuntimeError(
            f"No subjects found under {BEHAV_ROOT} (expected folders like SLB_000)."
        )

    for subj in subjects:
        subj_dir = BEHAV_ROOT / f"SLB_{subj}"
        if not subj_dir.exists():
            continue

        for cond, run_dir, task, run_label in TASK_RUNS:
            try:
                csv_path = find_csv(subj, cond, run_dir)
            except RuntimeError as e:
                print("[SKIP]", e)
                continue

            df = pd.read_csv(csv_path)
            label = f"sub-{subj} task-{task} run-{run_label} ({csv_path.name})"

            events = build_ol_events(df, carry_cols.copy(), event_specs, label)

            tsv = out_path(subj, task, run_label)
            tsv.parent.mkdir(parents=True, exist_ok=True)
            events.to_csv(tsv, sep="\t", index=False, float_format="%.3f")

            print("OK:", tsv)

    return 0


if __name__ == "__main__":
    sys.exit(main())
