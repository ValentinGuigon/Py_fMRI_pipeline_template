#!/usr/bin/env python3
import json
from pathlib import Path

import pandas as pd


# fMRIPrep derivatives root (contains sub-XXX/.../desc-confounds_timeseries.tsv)
DERIV = Path("/data/sld/homes/collab/slb/derivatives/fmriprep")

# Output model JSON (TM only)
OUT = Path("/data/sld/homes/vguigon/work/fitlins_models/tm_visual_vs_fixation_compcor_smdl.json")

SUBJECTS = ["000", "001", "002", "003"]
TASKS = ["tm1", "tm2"]  # TM only

# Conservative nuisance set: motion + FD + first 6 aCompCor
BASE_CONFOUNDS = [
    "framewise_displacement",
    "trans_x", "trans_y", "trans_z",
    "rot_x", "rot_y", "rot_z",
    "a_comp_cor_00", "a_comp_cor_01", "a_comp_cor_02",
    "a_comp_cor_03", "a_comp_cor_04", "a_comp_cor_05",
]

# Events from your master TM events.tsv
VISUAL_EVENTS = ["choice", "wait", "feedback"]
BASELINE_EVENT = "fixation"


def find_confounds_tsv(subj, task):
    """
    Robust-ish search for confounds TSV for a given subject/task.
    Avoid assuming exact session/run naming.
    """
    pattern = "sub-{0}/**/*task-{1}*_desc-confounds_timeseries.tsv".format(subj, task)
    return sorted(DERIV.glob(pattern))


def pick_one_confounds_tsv(paths):
    """
    If multiple matches exist, choose the shortest path as a heuristic for the primary file.
    Fail loudly if no matches.
    """
    if not paths:
        return None
    paths = sorted(paths, key=lambda p: len(str(p)))
    return paths[0]


def main():
    # Collect cosine regressors present anywhere in the targeted set
    cosine_cols = set()

    for subj in SUBJECTS:
        for task in TASKS:
            hits = find_confounds_tsv(subj, task)
            conf_tsv = pick_one_confounds_tsv(hits)
            if conf_tsv is None:
                raise RuntimeError(
                    "No confounds TSV found for sub-{0} task-{1} under {2}".format(subj, task, DERIV)
                )

            df = pd.read_csv(conf_tsv, sep="\t")
            for c in df.columns:
                if isinstance(c, str) and c.startswith("cosine"):
                    cosine_cols.add(c)

    confounds = list(BASE_CONFOUNDS) + sorted(cosine_cols)

    # Design regressors
    # Factor(trial_type) will create these columns if those trial_type values exist in events.tsv
    visual_regs = ["trial_type.{0}".format(ev) for ev in VISUAL_EVENTS]
    baseline_reg = "trial_type.{0}".format(BASELINE_EVENT)

    X = visual_regs + [baseline_reg] + confounds

    # Contrast: average(visual) - fixation
    # weights: [1/3, 1/3, 1/3, -1]
    w_visual = 1.0 / float(len(VISUAL_EVENTS))
    weights = [w_visual] * len(VISUAL_EVENTS) + [-1.0]

    # Validator-friendly pybids-transforms-v1 block (capitalized keys)
    transformations = {
        "Transformer": "pybids-transforms-v1",
        "Instructions": [
            {"Name": "Factor", "Input": ["trial_type"]},
            {
                "Name": "Convolve",
                "Input": [
                    "trial_type.choice",
                    "trial_type.wait",
                    "trial_type.feedback",
                    "trial_type.fixation",
                ],
                "Model": "spm",
            },
        ],
    }

    model = {
        "Name": "tmVisualVsFixationCompCor",
        "BIDSModelVersion": "1.0.0",
        "Description": (
            "TM visual epochs (choice+wait+feedback) contrasted against fixation, "
            "with motion + FD + aCompCor (+ cosine drifts if present)."
        ),
        "Input": {
            "task": TASKS,
            "subject": SUBJECTS
        },
        "Nodes": [
            {
                "Level": "Run",
                "Name": "runLevel",
                "GroupBy": ["subject", "run"],
                "Transformations": transformations,
                "Model": {
                    "X": X,
                    "Type": "glm"
                },
                "Contrasts": [
                    {
                        "Name": "visualGtFixation",
                        "ConditionList": visual_regs + [baseline_reg],
                        "Weights": weights,
                        "Test": "t"
                    }
                ]
            },
            {
                "Level": "Subject",
                "Name": "subjectLevel",
                "GroupBy": ["subject", "contrast"],
                "Model": {
                    "X": [1],
                    "Type": "meta"
                },
                "DummyContrasts": {
                    "Test": "t"
                }
            }
        ]
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(model, indent=2))

    print("Wrote:", str(OUT))
    print("Tasks:", TASKS)
    print("Regressors of interest:", len(visual_regs) + 1)
    print("Confounds included:", len(confounds))
    print("Cosines included:", len(cosine_cols))


if __name__ == "__main__":
    main()
