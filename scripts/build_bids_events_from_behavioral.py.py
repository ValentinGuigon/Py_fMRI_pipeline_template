#!/usr/bin/env python3
import sys
from pathlib import Path

import pandas as pd


# ---------- Paths ----------
BEHAV_ROOT = Path("/data/sld/homes/collab/slb/logs/fMRI/data/preprocessed/individual")
BIDS_ROOT = Path("/data/sld/homes/vguigon/work/slb_bids")

# ---------- Data dictionaries (3 separate) ----------
TRUST_DICT = Path("/data/sld/homes/vguigon/work/docs/Trust-DataDictionaryfMRI.csv")
OBSLEARN_DICT = Path("/data/sld/homes/vguigon/work/docs/OL-DataDictionaryfMRI.csv")
SOCIALRA_DICT = Path("/data/sld/homes/vguigon/work/docs/SRA-DataDictionaryfMRI.csv")

SUBJECTS = ["000", "001", "002", "003"]

# (condition dir, run dir, BIDS task label)
TASK_RUNS = [
    # ---- TRUST ----
    ("tm", "run-01", "tm1"),
    ("tm", "run-02", "tm2"),
    ("th", "run-01", "th1"),
    ("th", "run-02", "th2"),
    # ---- OBSLEARN ----
    ("ol", "run-01", "obslearn1"),
    ("ol", "run-02", "obslearn2"),
    # ---- socialRA ----
    ("socialRA", "run-self", "self"),
    ("socialRA", "run-social-01", "social1"),
    ("socialRA", "run-social-02", "social2"),
]


# ---------- Helpers ----------
def load_required_vars(data_dict_path: Path):
    dd = pd.read_csv(data_dict_path)
    req = dd.loc[dd["Required"].astype(str).str.strip().str.lower() == "yes", "Variable"]
    return [str(x) for x in req.tolist()]


def dict_for_condition(cond: str) -> Path:
    if cond in ("tm", "th"):
        return TRUST_DICT
    if cond == "ol":
        return OBSLEARN_DICT
    if cond == "socialRA":
        return SOCIALRA_DICT
    raise RuntimeError("No dictionary configured for condition '%s'" % cond)


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


def assert_no_missing_timestamps(trials: pd.DataFrame, timing_cols, label: str) -> None:
    """
    Fail if any selected trial row has NaN in any required timing anchor after numeric coercion.
    """
    missing_mask = trials[timing_cols].isna().any(axis=1)
    if missing_mask.any():
        bad_idx = missing_mask[missing_mask].index.tolist()[:10]
        bad = trials.loc[bad_idx, timing_cols]
        raise RuntimeError(
            "%s: missing timestamp(s) in required timing columns for trial-row indices %s.\n%s"
            % (label, bad_idx, bad)
        )


def finalize_events_df(out: pd.DataFrame, label: str) -> pd.DataFrame:
    first_cols = ["onset", "duration", "trial_type"]
    extra_cols = [c for c in out.columns if c not in first_cols]
    out = out.loc[:, first_cols + extra_cols]

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


# ---------- TRUST (tm/th) ----------
TR_COL_FIX = "scannerTimer_fixation1_Start"
TR_COL_CHOICE = "scannerTimer_choice_Start"
TR_COL_WAIT = "scannerTimer_wait_Start"
TR_COL_FB = "scannerTimer_feedback_Start"
TR_COL_END = "scannerTimer_trial_End"

TR_TIMING_COLS = [TR_COL_FIX, TR_COL_CHOICE, TR_COL_WAIT, TR_COL_FB, TR_COL_END]


def build_trust_events(df: pd.DataFrame, required_vars, label: str) -> pd.DataFrame:
    if TR_COL_FIX not in df.columns:
        raise RuntimeError("%s: missing column %s" % (label, TR_COL_FIX))

    trials = df.loc[~df[TR_COL_FIX].isna(), :].copy()

    if len(trials) == 0:
        raise RuntimeError("%s: found 0 trials (rows with %s)" % (label, TR_COL_FIX))

    trials = coerce_numeric(trials, TR_TIMING_COLS, label)
    assert_no_missing_timestamps(trials, TR_TIMING_COLS, label)

    trials = trials.sort_values(TR_COL_FIX, kind="mergesort").reset_index(drop=True)

    dur_fix = trials[TR_COL_CHOICE] - trials[TR_COL_FIX]
    dur_choice = trials[TR_COL_WAIT] - trials[TR_COL_CHOICE]
    dur_wait = trials[TR_COL_FB] - trials[TR_COL_WAIT]
    dur_fb = trials[TR_COL_END] - trials[TR_COL_FB]

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

    carry = [c for c in required_vars if c in trials.columns]

    rows = []
    for i in range(len(trials)):
        base = {"trial": int(i)}
        for c in carry:
            base[c] = trials.iloc[i][c]

        r = dict(base)
        r["onset"] = float(trials.iloc[i][TR_COL_FIX])
        r["duration"] = float(dur_fix.iloc[i])
        r["trial_type"] = "fixation"
        rows.append(r)

        r = dict(base)
        r["onset"] = float(trials.iloc[i][TR_COL_CHOICE])
        r["duration"] = float(dur_choice.iloc[i])
        r["trial_type"] = "choice"
        rows.append(r)

        r = dict(base)
        r["onset"] = float(trials.iloc[i][TR_COL_WAIT])
        r["duration"] = float(dur_wait.iloc[i])
        r["trial_type"] = "wait"
        rows.append(r)

        r = dict(base)
        r["onset"] = float(trials.iloc[i][TR_COL_FB])
        r["duration"] = float(dur_fb.iloc[i])
        r["trial_type"] = "feedback"
        rows.append(r)

    out = pd.DataFrame(rows).sort_values("onset", kind="mergesort").reset_index(drop=True)
    out = finalize_events_df(out, label)

    # internal consistency: 4 events per selected trial
    if len(out) != len(trials) * 4:
        raise RuntimeError("%s: internal mismatch (expected %d rows, got %d)" % (label, len(trials) * 4, len(out)))

    return out


# ---------- OBSLEARN (OL) ----------
# Trial definition: rows with a valid trialNb
OL_TRIALNB = "trialNb"
OL_TRTYPE = "trType"  # 1 = observe, 2 = play

# Observe-only anchors
OL_FIX_OB = "scannerTimer_fixation_ob_Start"
OL_SLOT = "scannerTimer_slot_machine_Start"
OL_WAIT_PARTNER = "scannerTimer_wait_partner_Start"
OL_PLAY_VIDEO = "scannerTimer_play_video_Start"
OL_FIX1 = "scannerTimer_fixation1_Start"

# Play-only anchors
OL_SELF_CHOICE = "scannerTimer_self_choice_Start"
OL_FEEDBACK = "scannerTimer_feedback_Start"

# Common
OL_TRIAL_END = "scannerTimer_trial_End"


def build_obslearn_events(df: pd.DataFrame, required_vars, label: str) -> pd.DataFrame:
    # required structure columns
    for c in [OL_TRIALNB, OL_TRTYPE, OL_TRIAL_END]:
        if c not in df.columns:
            raise RuntimeError("%s: missing required column %s" % (label, c))

    # select trial rows by trialNb, sort by trialNb
    trials = df.loc[~df[OL_TRIALNB].isna(), :].copy()
    if len(trials) == 0:
        raise RuntimeError("%s: found 0 trials (rows with %s)" % (label, OL_TRIALNB))

    trials[OL_TRIALNB] = pd.to_numeric(trials[OL_TRIALNB], errors="coerce")
    trials[OL_TRTYPE] = pd.to_numeric(trials[OL_TRTYPE], errors="coerce")

    if trials[OL_TRIALNB].isna().any():
        bad = trials[trials[OL_TRIALNB].isna()].index.tolist()[:10]
        raise RuntimeError("%s: non-numeric %s in trial rows at indices %s" % (label, OL_TRIALNB, bad))
    if trials[OL_TRTYPE].isna().any():
        bad = trials[trials[OL_TRTYPE].isna()].index.tolist()[:10]
        raise RuntimeError("%s: non-numeric %s in trial rows at indices %s" % (label, OL_TRTYPE, bad))

    # enforce expected trial types (1 observe, 2 play)
    bad_types = trials.loc[~trials[OL_TRTYPE].isin([1, 2]), [OL_TRIALNB, OL_TRTYPE]].head(10)
    if len(bad_types) > 0:
        raise RuntimeError("%s: unexpected trType values:\n%s" % (label, bad_types))

    # numeric coercion for timing columns (only those present)
    timing_cols = [
        OL_FIX_OB, OL_SLOT, OL_WAIT_PARTNER, OL_PLAY_VIDEO, OL_FIX1,
        OL_SELF_CHOICE, OL_FEEDBACK, OL_TRIAL_END
    ]
    trials = coerce_numeric(trials, timing_cols, label)

    # per-type required anchors
    req_observe = [OL_FIX_OB, OL_SLOT, OL_WAIT_PARTNER, OL_PLAY_VIDEO, OL_FIX1, OL_TRIAL_END]
    req_play = [OL_SELF_CHOICE, OL_FEEDBACK, OL_TRIAL_END]

    miss_ob = trials.loc[trials[OL_TRTYPE] == 1, req_observe].isna().any(axis=1)
    if miss_ob.any():
        bad_idx = trials.loc[trials[OL_TRTYPE] == 1].loc[miss_ob].index.tolist()[:10]
        bad = trials.loc[bad_idx, req_observe]
        raise RuntimeError(
            "%s: missing required timestamp(s) for trType==1 (observe) at indices %s.\n%s"
            % (label, bad_idx, bad)
        )

    miss_play = trials.loc[trials[OL_TRTYPE] == 2, req_play].isna().any(axis=1)
    if miss_play.any():
        bad_idx = trials.loc[trials[OL_TRTYPE] == 2].loc[miss_play].index.tolist()[:10]
        bad = trials.loc[bad_idx, req_play]
        raise RuntimeError(
            "%s: missing required timestamp(s) for trType==2 (play) at indices %s.\n%s"
            % (label, bad_idx, bad)
        )

    # stable order by trial number
    trials = trials.sort_values(OL_TRIALNB, kind="mergesort").reset_index(drop=True)

    carry = [c for c in required_vars if c in trials.columns]

    rows = []
    for i in range(len(trials)):
        base = {"trial": int(trials.iloc[i][OL_TRIALNB])}
        for c in carry:
            base[c] = trials.iloc[i][c]

        ttype = int(trials.iloc[i][OL_TRTYPE])

        if ttype == 1:
            # observe: fixation_ob -> slot_machine -> wait_partner -> play_video -> fixation1
            dur_observation = trials.iloc[i][OL_SLOT] - trials.iloc[i][OL_FIX_OB]
            dur_stimulus = trials.iloc[i][OL_WAIT_PARTNER] - trials.iloc[i][OL_SLOT]
            dur_outcome = trials.iloc[i][OL_FIX1] - trials.iloc[i][OL_PLAY_VIDEO]

            for name, dur in [("observation", dur_observation), ("stimulus", dur_stimulus), ("outcome", dur_outcome)]:
                if dur <= 0:
                    raise RuntimeError("%s: non-positive %s duration at trialNb=%s" % (label, name, str(base["trial"])))

            r = dict(base)
            r["onset"] = float(trials.iloc[i][OL_FIX_OB])
            r["duration"] = float(dur_observation)
            r["trial_type"] = "observation"
            rows.append(r)

            r = dict(base)
            r["onset"] = float(trials.iloc[i][OL_SLOT])
            r["duration"] = float(dur_stimulus)
            r["trial_type"] = "stimulus"
            rows.append(r)

            r = dict(base)
            r["onset"] = float(trials.iloc[i][OL_PLAY_VIDEO])
            r["duration"] = float(dur_outcome)
            r["trial_type"] = "outcome"
            rows.append(r)

        elif ttype == 2:
            # play: self_choice -> feedback -> trial_end
            dur_choice = trials.iloc[i][OL_FEEDBACK] - trials.iloc[i][OL_SELF_CHOICE]
            dur_feedback = trials.iloc[i][OL_TRIAL_END] - trials.iloc[i][OL_FEEDBACK]

            for name, dur in [("choice", dur_choice), ("feedback", dur_feedback)]:
                if dur <= 0:
                    raise RuntimeError("%s: non-positive %s duration at trialNb=%s" % (label, name, str(base["trial"])))

            r = dict(base)
            r["onset"] = float(trials.iloc[i][OL_SELF_CHOICE])
            r["duration"] = float(dur_choice)
            r["trial_type"] = "choice"
            rows.append(r)

            r = dict(base)
            r["onset"] = float(trials.iloc[i][OL_FEEDBACK])
            r["duration"] = float(dur_feedback)
            r["trial_type"] = "feedback"
            rows.append(r)

        else:
            # should be impossible due to checks above
            raise RuntimeError("%s: unexpected trType=%s at trialNb=%s" % (label, str(ttype), str(base["trial"])))

    out = pd.DataFrame(rows).sort_values("onset", kind="mergesort").reset_index(drop=True)
    out = finalize_events_df(out, label)
    return out


# ---------- socialRA ----------
SRA_BLOCK = "block"                 # 1=self, 2=social
SRA_TRIALNB = "trials_inblock"      # 1..N within run

# Self block anchors
SRA_SELF_START = "scannerTimer_self_Start"
SRA_SELF_CONFIRM = "scannerTimer_self_confirm_Start"

# Social block anchors
SRA_INFOSEEK = "scannerTimer_social_infoseek_Start"
SRA_INFOSEEK_CONFIRM = "scannerTimer_social_infoseek_confirm_Start"
SRA_JIT1 = "scannerTimer_jitter_social_1_Start"
SRA_INFO = "scannerTimer_social_info_Start"
SRA_JIT2 = "scannerTimer_jitter_social_2_Start"
SRA_SOC_CHOICE = "scannerTimer_social_choice_Start"
SRA_SOC_CHOICE_CONFIRM = "scannerTimer_social_choice_confirm_Start"
SRA_JIT3 = "scannerTimer_jitter_social_3_Start"

# Common
SRA_TRIAL_END = "scannerTimer_trial_End"


def build_socialra_events(df: pd.DataFrame, required_vars, label: str) -> pd.DataFrame:
    # required structure columns
    for c in [SRA_BLOCK, SRA_TRIALNB, SRA_TRIAL_END]:
        if c not in df.columns:
            raise RuntimeError("%s: missing required column %s" % (label, c))

    # select trial rows by trials_inblock
    trials = df.loc[~df[SRA_TRIALNB].isna(), :].copy()
    if len(trials) == 0:
        raise RuntimeError("%s: found 0 trials (rows with %s)" % (label, SRA_TRIALNB))

    # coerce identifiers
    trials[SRA_BLOCK] = pd.to_numeric(trials[SRA_BLOCK], errors="coerce")
    trials[SRA_TRIALNB] = pd.to_numeric(trials[SRA_TRIALNB], errors="coerce")

    if trials[SRA_BLOCK].isna().any():
        bad = trials.loc[trials[SRA_BLOCK].isna(), [SRA_TRIALNB]].head(10)
        raise RuntimeError("%s: non-numeric %s in trial rows:\n%s" % (label, SRA_BLOCK, bad))
    if trials[SRA_TRIALNB].isna().any():
        bad = trials.loc[trials[SRA_TRIALNB].isna(), [SRA_BLOCK]].head(10)
        raise RuntimeError("%s: non-numeric %s in trial rows:\n%s" % (label, SRA_TRIALNB, bad))

    bad_blocks = trials.loc[~trials[SRA_BLOCK].isin([1, 2]), [SRA_TRIALNB, SRA_BLOCK]].head(10)
    if len(bad_blocks) > 0:
        raise RuntimeError("%s: unexpected block values:\n%s" % (label, bad_blocks))

    # coerce all timing columns we might use (only if present)
    timing_cols = [
        SRA_SELF_START, SRA_SELF_CONFIRM,
        SRA_INFOSEEK, SRA_INFOSEEK_CONFIRM, SRA_JIT1, SRA_INFO, SRA_JIT2,
        SRA_SOC_CHOICE, SRA_SOC_CHOICE_CONFIRM, SRA_JIT3,
        SRA_TRIAL_END
    ]
    present = [c for c in timing_cols if c in trials.columns]
    trials = coerce_numeric(trials, present, label)

    # per-block required anchors
    req_self = [SRA_SELF_START, SRA_SELF_CONFIRM, SRA_TRIAL_END]
    req_social = [
        SRA_INFOSEEK, SRA_INFOSEEK_CONFIRM, SRA_JIT1, SRA_INFO, SRA_JIT2,
        SRA_SOC_CHOICE, SRA_SOC_CHOICE_CONFIRM, SRA_JIT3, SRA_TRIAL_END
    ]

    miss_self = trials.loc[trials[SRA_BLOCK] == 1, req_self].isna().any(axis=1)
    if miss_self.any():
        bad_idx = trials.loc[trials[SRA_BLOCK] == 1].loc[miss_self].index.tolist()[:10]
        bad = trials.loc[bad_idx, req_self]
        raise RuntimeError(
            "%s: missing required timestamp(s) for socialRA self block (block==1) at indices %s.\n%s"
            % (label, bad_idx, bad)
        )

    miss_social = trials.loc[trials[SRA_BLOCK] == 2, req_social].isna().any(axis=1)
    if miss_social.any():
        bad_idx = trials.loc[trials[SRA_BLOCK] == 2].loc[miss_social].index.tolist()[:10]
        bad = trials.loc[bad_idx, req_social]
        raise RuntimeError(
            "%s: missing required timestamp(s) for socialRA social block (block==2) at indices %s.\n%s"
            % (label, bad_idx, bad)
        )

    # stable order by trials_inblock
    trials = trials.sort_values(SRA_TRIALNB, kind="mergesort").reset_index(drop=True)

    carry = [c for c in required_vars if c in trials.columns]

    rows = []
    for i in range(len(trials)):
        base = {"trial": int(trials.iloc[i][SRA_TRIALNB])}
        for c in carry:
            base[c] = trials.iloc[i][c]

        block = int(trials.iloc[i][SRA_BLOCK])

        if block == 1:
            # self: self_start -> self_confirm, then post-confirm interval to trial_end (label it "iti")
            dur_self = trials.iloc[i][SRA_SELF_CONFIRM] - trials.iloc[i][SRA_SELF_START]
            dur_iti = trials.iloc[i][SRA_TRIAL_END] - trials.iloc[i][SRA_SELF_CONFIRM]

            if dur_self <= 0:
                raise RuntimeError("%s: non-positive self_decision duration at trial=%s" % (label, str(base["trial"])))
            if dur_iti <= 0:
                raise RuntimeError("%s: non-positive iti duration at trial=%s" % (label, str(base["trial"])))

            r = dict(base)
            r["onset"] = float(trials.iloc[i][SRA_SELF_START])
            r["duration"] = float(dur_self)
            r["trial_type"] = "self_decision"
            rows.append(r)

            r = dict(base)
            r["onset"] = float(trials.iloc[i][SRA_SELF_CONFIRM])
            r["duration"] = float(dur_iti)
            r["trial_type"] = "iti"
            rows.append(r)

        elif block == 2:
            # social: infoseek epoch, info epoch, social_choice epoch, then final jitter->trial_end as "iti"
            dur_infoseek = trials.iloc[i][SRA_INFOSEEK_CONFIRM] - trials.iloc[i][SRA_INFOSEEK]
            dur_info = trials.iloc[i][SRA_SOC_CHOICE] - trials.iloc[i][SRA_INFO]
            dur_social_choice = trials.iloc[i][SRA_SOC_CHOICE_CONFIRM] - trials.iloc[i][SRA_SOC_CHOICE]
            dur_iti = trials.iloc[i][SRA_TRIAL_END] - trials.iloc[i][SRA_JIT3]

            if dur_infoseek <= 0:
                raise RuntimeError("%s: non-positive social_infoseek duration at trial=%s" % (label, str(base["trial"])))
            if dur_info <= 0:
                raise RuntimeError("%s: non-positive social_info duration at trial=%s" % (label, str(base["trial"])))
            if dur_social_choice <= 0:
                raise RuntimeError("%s: non-positive social_choice duration at trial=%s" % (label, str(base["trial"])))
            if dur_iti <= 0:
                raise RuntimeError("%s: non-positive iti duration at trial=%s" % (label, str(base["trial"])))

            r = dict(base)
            r["onset"] = float(trials.iloc[i][SRA_INFOSEEK])
            r["duration"] = float(dur_infoseek)
            r["trial_type"] = "social_infoseek"
            rows.append(r)

            r = dict(base)
            r["onset"] = float(trials.iloc[i][SRA_INFO])
            r["duration"] = float(dur_info)
            r["trial_type"] = "social_info"
            rows.append(r)

            r = dict(base)
            r["onset"] = float(trials.iloc[i][SRA_SOC_CHOICE])
            r["duration"] = float(dur_social_choice)
            r["trial_type"] = "social_choice"
            rows.append(r)

            r = dict(base)
            r["onset"] = float(trials.iloc[i][SRA_JIT3])
            r["duration"] = float(dur_iti)
            r["trial_type"] = "iti"
            rows.append(r)

        else:
            # impossible due to checks above
            raise RuntimeError("%s: unexpected block=%s at trial=%s" % (label, str(block), str(base["trial"])))

    out = pd.DataFrame(rows).sort_values("onset", kind="mergesort").reset_index(drop=True)
    out = finalize_events_df(out, label)
    return out


# ---------- Dispatcher ----------
def build_events_for_condition(df: pd.DataFrame, cond: str, required_vars, label: str) -> pd.DataFrame:
    if cond in ("tm", "th"):
        return build_trust_events(df, required_vars, label)
    if cond == "ol":
        return build_obslearn_events(df, required_vars, label)
    if cond == "socialRA":
        return build_socialra_events(df, required_vars, label)
    raise RuntimeError("%s: unsupported condition '%s'" % (label, cond))


def main():
    # validate dictionaries exist
    for p in [TRUST_DICT, OBSLEARN_DICT, SOCIALRA_DICT]:
        if not p.exists():
            raise RuntimeError("Data dictionary not found at %s" % p)

    required_cache = {}

    for subj in SUBJECTS:
        for cond, run, task in TASK_RUNS:
            csv_path = find_unique_csv(subj, cond, run)
            df = pd.read_csv(csv_path)

            dd_path = dict_for_condition(cond)
            if dd_path not in required_cache:
                required_cache[dd_path] = load_required_vars(dd_path)
            required_vars = required_cache[dd_path]

            label = "sub-%s cond-%s task-%s (%s)" % (subj, cond, task, csv_path.name)
            events = build_events_for_condition(df, cond, required_vars, label)

            out_tsv = out_path(subj, task)
            out_tsv.parent.mkdir(parents=True, exist_ok=True)
            events.to_csv(out_tsv, sep="\t", index=False, float_format="%.3f")

            print("OK:", out_tsv)


if __name__ == "__main__":
    sys.exit(main())
