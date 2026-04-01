#!/usr/bin/env python3
"""
build_sra_bids_events.py

Converts preprocessed SRA behavioral CSVs into BIDS-compliant events.tsv files.

Logic is dictionary-driven:
  - "Pivot to onset" rows define onset columns
  - matching "Pivot to duration" rows define duration columns
  - "trial_type label in .tsv file" defines BIDS trial_type
  - "Separate column" rows are copied into every output event row

Self block (task-riskself, single acquisition, no run index in filename):
  self_choice             -> participant choice between safe and gamble
  self_choice_validation  -> confirmation screen (self_responded == 1)
  self_missed             -> missed screen (self_responded == 0,
                             replaces self_choice_validation)
  self_iti                -> inter-trial fixation

Social block (task-risksocial, two runs):
  social_infoseek              -> slider for info-seek probability
  social_infoseek_validation   -> infoseek confirmation (social_infoseek_responded == 1)
  social_infoseek_missed       -> collapsed event spanning infoseek_validation +
                                  fixation1 + social_info
                                  (social_infoseek_responded == 0)
  social_fixation1             -> fixation before social info
                                  (suppressed when social_infoseek_responded == 0)
  social_info                  -> social info display
                                  (suppressed when social_infoseek_responded == 0)
  social_fixation2             -> fixation before social choice
  social_choice                -> participant choice between safe and gamble
  social_choice_validation     -> confirmation screen (social_choice_responded == 1)
  social_choice_missed         -> missed screen (social_choice_responded == 0,
                                  replaces social_choice_validation)
  social_iti                   -> inter-trial fixation

Notes:
  - Self and social blocks are in separate CSV files and produce separate BIDS outputs.
  - Block membership is determined by TASK_RUNS, not from a column in the data.
  - Miss handling is hardcoded; it cannot be expressed in the dictionary schema.
  - Infoseek and choice misses in the social block are independent and can co-occur.
  - These are canonical/base task events only. No GLM enrichment is performed here.
"""

import sys
from pathlib import Path

import pandas as pd


# ---------- Paths ----------
BEHAV_ROOT = Path("/data/sld/homes/collab/slb/behav_data/fMRI/data")
BIDS_ROOT = Path("/data/sld/homes/vguigon/slb_work/slb_bids_runs")
SRA_DICT = Path("/data/sld/homes/vguigon/slb_work/docs/dictionaries/SRA-DataDictionaryfMRI.csv")

# ---------- SRA runs ----------
# (cond_folder, run_folder, BIDS task label, BIDS run label or None, block)
TASK_RUNS = [
    ("socialra", "run-self", "riskself", None, "self"),
    ("socialra", "run-social-01", "risksocial", "01", "social"),
    ("socialra", "run-social-02", "risksocial", "02", "social"),
]

# ---------- Missed-trial constants ----------
# Self block: missed choice
SELF_MISS_SUPPRESSED = {"self_choice_validation"}
SELF_MISS_ONSET_COL = "scannerTimer_self_confirm_Start"
SELF_MISS_DUR_COLS = ["selfConfirmationDur"]

# Social block: missed infoseek slider
INFOSEEK_MISS_SUPPRESSED = {
    "social_infoseek_validation",
    "social_fixation1",
    "social_info",
}
INFOSEEK_MISS_ONSET_COL = "scannerTimer_social_infoseek_confirm_Start"
INFOSEEK_MISS_DUR_COLS = [
    "socialInfoConfirmDur",
    "socialJitter1Dur",
    "socialInfoDisplayDur",
]

# Social block: missed choice
SOCIAL_CHOICE_MISS_SUPPRESSED = {"social_choice_validation"}
SOCIAL_CHOICE_MISS_ONSET_COL = "scannerTimer_social_choice_confirm_Start"
SOCIAL_CHOICE_MISS_DUR_COLS = ["socialChoiceConfirmDur"]


# ---------- RT cleaning ----------
def clean_rt_columns(df):
    """
    Convert RT columns stored like "[0.532]" or "[0.532, 0.600]"
    into scalar numeric values.
    """
    rt_cols = ["self_RT", "social_choice_RT"]

    for col in rt_cols:
        if col not in df.columns:
            continue

        def _extract(x):
            if pd.isna(x):
                return x

            s = str(x).strip()
            s = s.replace("[", "").replace("]", "")

            if "," in s:
                s = s.split(",")[0]

            try:
                return float(s)
            except Exception:
                return pd.NA

        df[col] = df[col].apply(_extract)

    return df


# ---------- Dictionary helpers ----------
def load_sra_dictionary(data_dict_path):
    """
    Returns:
      carry_specs: list[dict]
        Columns to preserve in the output events.tsv.
        Only rows marked "Separate column" are carried.
        Each dict has:
          - col
          - block_scope

      event_specs: list[dict], each with:
          onset_col, duration_col, trial_type
    """
    dd = pd.read_csv(data_dict_path)

    required_cols = ["Variable", "Keep in .tsv file", "trial_type label in .tsv file"]
    for c in required_cols:
        if c not in dd.columns:
            raise RuntimeError(f"Dictionary missing required column: {c}")

    block_col = None
    for c in dd.columns:
        if str(c).strip().lower() == "in block (self/social)":
            block_col = c
            break

    keep = dd["Keep in .tsv file"].astype(str).str.strip()

    # Preserve only true metadata columns, not pivot source columns
    carry_rows = dd.loc[keep == "Separate column", :].copy()
    carry_specs = []
    for _, row in carry_rows.iterrows():
        carry_specs.append(
            {
                "col": str(row["Variable"]).strip(),
                "block_scope": str(row[block_col]).strip() if block_col is not None else "",
            }
        )

    onset_rows = dd.loc[keep == "Pivot to onset", :].copy()
    dur_rows = dd.loc[keep == "Pivot to duration", :].copy()

    if len(onset_rows) == 0:
        raise RuntimeError("No 'Pivot to onset' rows found in SRA dictionary")
    if len(dur_rows) == 0:
        raise RuntimeError("No 'Pivot to duration' rows found in SRA dictionary")

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

    return carry_specs, event_specs


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
        raise RuntimeError(f"Condition dir not found for sub={subj} cond={cond}")

    pre_dir = cond_path / "preprocessed"
    label = f"sub-{subj} cond={cond} run={run_dir}"

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
        f"No .csv found for {label} (checked {run_path}, {pre_dir}, {cond_path})"
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
    if run_label is None:
        fname = f"sub-{subj}_task-{task}_events.tsv"
    else:
        fname = f"sub-{subj}_task-{task}_run-{run_label}_events.tsv"
    return func_dir / fname


# ---------- SRA builder ----------
def label_allowed_for_block(trial_type, block):
    """
    Restrict dictionary-driven SRA labels by block.
    Self events only fire in the self block; social events only in the social block.
    """
    if trial_type.startswith("self_"):
        return block == "self"
    if trial_type.startswith("social_"):
        return block == "social"
    return True


def column_allowed_for_block(block_scope, block):
    """
    Use dictionary column 'In Block (self/social)' to decide whether a carried
    metadata column should appear in the self or social output.
    """
    s = str(block_scope).strip().lower()

    if s in {"", "both", "self/social", "all", "nan"}:
        return True
    if s == "self":
        return block == "self"
    if s == "social":
        return block == "social"
    if "self" in s and "social" in s:
        return True
    if "self" in s:
        return block == "self"
    if "social" in s:
        return block == "social"

    return True


def build_sra_events(df, carry_specs, event_specs, block, label):
    block_specs = [
        spec for spec in event_specs
        if label_allowed_for_block(spec["trial_type"], block)
    ]

    if not block_specs:
        raise RuntimeError(f"{label}: no event specs found for block={block}")

    responded_cols = (
        ["self_responded"] if block == "self"
        else ["social_infoseek_responded", "social_choice_responded"]
    )

    needed_cols = []
    for spec in block_specs:
        needed_cols.extend([spec["onset_col"], spec["duration_col"]])

    if block == "self":
        needed_cols.extend([SELF_MISS_ONSET_COL] + SELF_MISS_DUR_COLS)
    else:
        needed_cols.extend([INFOSEEK_MISS_ONSET_COL] + INFOSEEK_MISS_DUR_COLS)
        needed_cols.extend([SOCIAL_CHOICE_MISS_ONSET_COL] + SOCIAL_CHOICE_MISS_DUR_COLS)

    needed_cols.extend(responded_cols)
    needed_cols = sorted(set(needed_cols))

    df = coerce_numeric(df, needed_cols, label)

    onset_cols = [spec["onset_col"] for spec in block_specs]
    present_onset_cols = [c for c in onset_cols if c in df.columns]
    if not present_onset_cols:
        raise RuntimeError(
            f"{label}: none of the expected onset columns were found. "
            f"Expected one of: {onset_cols}"
        )

    trial_mask = df[present_onset_cols].notna().any(axis=1)
    trials = df.loc[trial_mask, :].copy()

    if len(trials) == 0:
        raise RuntimeError(f"{label}: found 0 trials (no non-null onset rows)")

    for col in responded_cols:
        bad = trials.loc[~trials[col].isin([0, 1]), [col]].head(10)
        if len(bad) > 0:
            raise RuntimeError(
                f"{label}: unexpected values in '{col}' (expected 0 or 1):\n{bad}"
            )

    trials["_sort_onset"] = trials[present_onset_cols].min(axis=1, skipna=True)
    trials = trials.sort_values("_sort_onset", kind="mergesort").reset_index(drop=True)
    trials = trials.drop(columns=["_sort_onset"])

    carry = [
        spec["col"]
        for spec in carry_specs
        if column_allowed_for_block(spec["block_scope"], block) and spec["col"] in trials.columns
    ]

    rows = []
    for i in range(len(trials)):
        t = trials.iloc[i]
        base = {c: t[c] for c in carry}

        if block == "self":
            is_self_miss = int(t["self_responded"]) == 0
            is_infoseek_miss = False
            is_social_choice_miss = False
        else:
            is_self_miss = False
            is_infoseek_miss = int(t["social_infoseek_responded"]) == 0
            is_social_choice_miss = int(t["social_choice_responded"]) == 0

        suppressed = set()
        if is_self_miss:
            suppressed |= SELF_MISS_SUPPRESSED
        if is_infoseek_miss:
            suppressed |= INFOSEEK_MISS_SUPPRESSED
        if is_social_choice_miss:
            suppressed |= SOCIAL_CHOICE_MISS_SUPPRESSED

        emitted_this_row = 0

        for spec in block_specs:
            onset_col = spec["onset_col"]
            duration_col = spec["duration_col"]
            trial_type = spec["trial_type"]

            if trial_type in suppressed:
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
                dict(base, onset=float(onset), duration=float(duration), trial_type=trial_type)
            )
            emitted_this_row += 1

        if is_self_miss:
            miss_onset = t[SELF_MISS_ONSET_COL]
            miss_dur = sum(t[c] for c in SELF_MISS_DUR_COLS)
            if pd.isna(miss_onset):
                raise RuntimeError(
                    f"{label}: missing {SELF_MISS_ONSET_COL} on self_missed trial row {i}"
                )
            if any(pd.isna(t[c]) for c in SELF_MISS_DUR_COLS):
                raise RuntimeError(
                    f"{label}: missing duration component for 'self_missed' at trial row {i}"
                )
            rows.append(
                dict(
                    base,
                    onset=float(miss_onset),
                    duration=float(miss_dur),
                    trial_type="self_missed",
                )
            )
            emitted_this_row += 1

        if is_infoseek_miss:
            miss_onset = t[INFOSEEK_MISS_ONSET_COL]
            miss_dur = sum(t[c] for c in INFOSEEK_MISS_DUR_COLS)
            if pd.isna(miss_onset):
                raise RuntimeError(
                    f"{label}: missing {INFOSEEK_MISS_ONSET_COL} on social_infoseek_missed trial row {i}"
                )
            if any(pd.isna(t[c]) for c in INFOSEEK_MISS_DUR_COLS):
                raise RuntimeError(
                    f"{label}: missing duration component for 'social_infoseek_missed' at trial row {i}"
                )
            rows.append(
                dict(
                    base,
                    onset=float(miss_onset),
                    duration=float(miss_dur),
                    trial_type="social_infoseek_missed",
                )
            )
            emitted_this_row += 1

        if is_social_choice_miss:
            miss_onset = t[SOCIAL_CHOICE_MISS_ONSET_COL]
            miss_dur = sum(t[c] for c in SOCIAL_CHOICE_MISS_DUR_COLS)
            if pd.isna(miss_onset):
                raise RuntimeError(
                    f"{label}: missing {SOCIAL_CHOICE_MISS_ONSET_COL} on social_choice_missed trial row {i}"
                )
            if any(pd.isna(t[c]) for c in SOCIAL_CHOICE_MISS_DUR_COLS):
                raise RuntimeError(
                    f"{label}: missing duration component for 'social_choice_missed' at trial row {i}"
                )
            rows.append(
                dict(
                    base,
                    onset=float(miss_onset),
                    duration=float(miss_dur),
                    trial_type="social_choice_missed",
                )
            )
            emitted_this_row += 1

        if emitted_this_row == 0:
            raise RuntimeError(
                f"{label}: no valid events emitted for trial row {i} (block={block})"
            )

    if len(rows) == 0:
        raise RuntimeError(f"{label}: no event rows were produced")

    out = pd.DataFrame(rows).sort_values("onset", kind="mergesort").reset_index(drop=True)
    out = finalize_events_df(out, label)
    return out


# ---------- Main ----------
def main():
    if not SRA_DICT.exists():
        raise RuntimeError(f"SRA data dictionary not found at {SRA_DICT}")

    carry_specs, event_specs = load_sra_dictionary(SRA_DICT)

    print("[INFO] Preserved columns:")
    print("       " + ", ".join(spec["col"] for spec in carry_specs))
    print("[INFO] Event specs:")
    for spec in event_specs:
        print(f"       {spec['onset_col']} + {spec['duration_col']} -> {spec['trial_type']}")

    subjects = discover_subjects_from_behav_root()
    if not subjects:
        raise RuntimeError(
            f"No subjects found under {BEHAV_ROOT} (expected folders like SLB_000)."
        )

    for subj in subjects:
        subj_dir = BEHAV_ROOT / f"SLB_{subj}"
        if not subj_dir.exists():
            continue

        for cond, run_dir, task, run_label, block in TASK_RUNS:
            try:
                csv_path = find_csv(subj, cond, run_dir)
            except RuntimeError as e:
                print("[SKIP]", e)
                continue

            df = pd.read_csv(csv_path)
            df.columns = df.columns.astype(str).str.strip()
            df = clean_rt_columns(df)

            label = f"sub-{subj} task-{task} block-{block} ({csv_path.name})"

            events = build_sra_events(df, carry_specs.copy(), event_specs, block, label)

            tsv = out_path(subj, task, run_label)
            tsv.parent.mkdir(parents=True, exist_ok=True)
            events.to_csv(tsv, sep="\t", index=False, float_format="%.3f")

            print("OK:", tsv)

    return 0


if __name__ == "__main__":
    sys.exit(main())
