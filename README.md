# FitLins pipeline

This project requires a **specific, reproducible FitLins stack** due to incompatibilities between vanilla FitLins/PyBIDS and the structure of the SLB dataset.

---

## Required components

### 1. `rebuild_bids_thinclone.py`
Creates a clean *working* BIDS directory (`slb_bids`) by:
- Symlinking imaging files (NIfTI) from the shared dataset
- Materializing all metadata (JSON/TSV) locally
- Avoiding mixed provenance between symlinked files and locally edited metadata

---

### 2. `containers/fitlins_patched/`
Contains:
- `fitlins_patched.def`
- `environment.yml`
- Built image: `fitlins-0.11.0_pybids-0.15.6_patched.sif` with
```bash
mv -n fitlins_patched.sif fitlins-0.11.0_pybids-0.15.6_patched.sif
```
- Optional checksum (`.sha256`)with
```bash
sha256sum fitlins-0.11.0_pybids-0.15.6_patched.sif > fitlins-0.11.0_pybids-0.15.6_patched.sif.sha256
```

Purpose:
- Pin a known-good FitLins stack (`fitlins==0.11.0`)
- Use a compatible PyBIDS version
- Apply a defensive patch against **dict-valued entities** leaking into
  `pandas.DataFrame.query`, which otherwise crashes FitLins

> **Important:**  
> Vanilla FitLins fails on this dataset with  
> `NotImplementedError: 'Dict' nodes are not implemented`.  
> The patched container sanitizes entity dictionaries and restores stable behavior.

---

### 3. FitLins model JSON files (`fitlins_models/*.json`)
- BIDS Stats Models compliant

---

### 4. `run_fitlins_models.sh`
A single **agnostic runner script** that:
- Works for any task or model JSON
- Explicitly passes all paths and resources
- Avoids hard-coded assumptions
- Invokes FitLins in this project

Collaborators should **not** call `fitlins` directly.

---

### Canonical execution example

```bash
/data/sld/homes/vguigon/work/scripts/run_fitlins_models.sh \
  --bids-dir /data/sld/homes/vguigon/work/slb_bids \
  --model /data/sld/homes/vguigon/work/fitlins_models/tm_visual_vs_fixation_compcor_smdl.json \
  --subjects "000 001 002 003" \
  --deriv-root /data/sld/homes/collab/slb/derivatives \
  --deriv-subdir fmriprep \
  --deriv-label fmriprep \
  --out-parent /data/sld/homes/vguigon/work/fitlins_derivatives \
  --smooth "6:run:iso" \
  --ncpus 8 \
  --mem-gb 16 \
  --debug \
  --out-suffix tm_visual_vs_fixation_compcor_s6
```

## Run pipeline steps

Note 1: steps available: 
- all
- fitlins
- fixreport
- plot-run
- plot-group

Note 2: Do not include spacing between steps

Note 3: Add `--force` to the steps if you want to overwrite

### 1. Run everything
```bash
/data/sld/homes/vguigon/work/scripts/run_pipeline.sh \
  --model-stem tm_visual_vs_fixation_compcor \
  --model-json /data/sld/homes/vguigon/work/fitlins_models/tm_visual_vs_fixation_compcor_smdl.json \
  --subjects "000 001 002 003" \
  --bids-dir /data/sld/homes/vguigon/work/slb_bids \
  --deriv-root /data/sld/homes/collab/slb/derivatives \
  --deriv-subdir fmriprep \
  --deriv-label fmriprep \
  --out-parent /data/sld/homes/vguigon/work/fitlins_derivatives \
  --out-suffix tm_visual_vs_fixation_compcor_s6 \
  --smooth "6:run:iso" \
  --space MNI152NLin2009cAsym \
  --ncpus 8 --mem-gb 16 --debug \
  --plot-nodes-group "node-subjectLevel" \
  --plot-glob '**/*stat-t*_statmap.nii*' \
  --plot-outdir-group /data/sld/homes/vguigon/work/figures/tm_group_visualGtFixation_s6_t_p001 \
  --contrasts "visualGtFixation" \
  --thr-mode p-unc --p-unc 0.001 \
  --display-mode ortho \
  --steps all --force
```

### 2. Run only Fitlins
```bash
/data/sld/homes/vguigon/work/scripts/run_pipeline.sh \
  --model-stem tm_visual_vs_fixation_compcor \
  --subjects "000 001 002 003" \
  --smooth "6:run:iso" \
  --steps fitlins
```

### 3. Plot only
```bash
/data/sld/homes/vguigon/work/scripts/run_pipeline.sh \
  --model-stem tm_visual_vs_fixation_compcor \
  --subjects "000 001 002 003" \
  --smooth "6:run:iso" \
  --steps plot-run,plot-group
```

#### p-unc, infer df automatically
```bash
/data/sld/homes/vguigon/work/scripts/run_pipeline.sh --model-stem tm_visual_vs_fixation_compcor --subjects "000 001" \
  --smooth 6:run:iso --thr-mode p-unc --p-unc 0.005
```

#### p-unc but force df (reproducible + faster)
```bash
/data/sld/homes/vguigon/work/scripts/run_pipeline.sh --model-stem tm_visual_vs_fixation_compcor --subjects "000 001" \
  --thr-mode p-unc --p-unc 0.001 --df 180
```

#### fixed threshold
```bash
/data/sld/homes/vguigon/work/scripts/run_pipeline.sh --model-stem tm_visual_vs_fixation_compcor --subjects "000 001" \
  --thr-mode fixed --thr-fixed 3.5
```

#### no threshold
```bash
/data/sld/homes/vguigon/work/scripts/run_pipeline.sh --model-stem tm_visual_vs_fixation_compcor --subjects "000 001" \
  --thr-mode none
```


## OR: End-to-end FitLins analysis workflow

### 1. Define model and analysis
```bash
MODEL=tm_visual_vs_fixation_compcor

# Smoothing spec passed to FitLins
SMOOTH="6:run:iso"

# Derive output suffix automatically
# Convention: <model>_s<kernel-mm>
KERNEL_MM=$(echo "${SMOOTH}" | cut -d: -f1)
OUT_SUFFIX="${MODEL}_s${KERNEL_MM}"
```

### 1. Create the BIDS Stats Model
```bash
python3 /data/sld/homes/vguigon/work/scripts/make_model_tmth_visual_vs_fixation_compcor.py
```

### 2. Run FitLins (Apptainer container)
```bash
/data/sld/homes/vguigon/work/scripts/run_fitlins_models.sh \
  --bids-dir /data/sld/homes/vguigon/work/slb_bids \
  --model /data/sld/homes/vguigon/work/fitlins_models/${MODEL}_smdl.json \
  --subjects "000 001 002 003" \
  --deriv-root /data/sld/homes/collab/slb/derivatives \
  --deriv-subdir fmriprep \
  --deriv-label fmriprep \
  --out-parent /data/sld/homes/vguigon/work/fitlins_derivatives \
  --smooth "${SMOOTH}" \
  --ncpus 8 \
  --mem-gb 16 \
  --debug \
  --out-suffix "${OUT_SUFFIX}"
```

### 3. Fix FitLins HTML report
```bash
python3 fix_fitlins_reports.py \
  /data/sld/homes/vguigon/work/fitlins_derivatives/${OUT_SUFFIX}/reports/model-${MODEL}.html \
  --verbose
```

### 4. Plots

#### Plot run-level maps
```bash
python3 /data/sld/homes/vguigon/work/scripts/plot_fmri_statmaps.py \
  --root /data/sld/homes/vguigon/work/fitlins_derivatives/${OUT_SUFFIX} \
  --nodes node-runLevel \
  --glob '**/*stat-t*_statmap.nii*' \
  --outdir /data/sld/homes/vguigon/work/figures/${OUT_SUFFIX}_run_t_p001 \
  --subjects 000 001 002 003 \
  --contrasts visual_gt_fixation \
  --thr-mode p-unc \
  --p-unc 0.001 \
  --two-sided \
  --display-mode ortho \
  --plot-abs
```

#### Plot subject-level maps
```bash
python3 /data/sld/homes/vguigon/work/scripts/plot_fmri_statmaps.py \
  --root /data/sld/homes/vguigon/work/fitlins_derivatives/${OUT_SUFFIX} \
  --nodes node-subjectLevel \
  --glob '**/*stat-t*_statmap.nii*' \
  --outdir /data/sld/homes/vguigon/work/figures/${OUT_SUFFIX}_group_t_p001 \
  --contrasts visual_gt_fixation \
  --thr-mode p-unc \
  --p-unc 0.001 \
  --two-sided \
  --display-mode ortho \
  --plot-abs
```

#### Optional thresholding modes
- Fixed threshold: `--thr-mode fixed --thr-fixed 4.0`
- No thresholding: `--thr-mode none`