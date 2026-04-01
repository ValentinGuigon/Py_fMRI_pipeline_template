# Environment Reference

---

## Patched container (required)

| | |
|---|---|
| **SIF path** | `{WORK_ROOT}/containers/fitlins_patched/fitlins-0.11.0_pybids-0.15.6_patched.sif` |
| **Definition file** | `containers/fitlins_patched/fitlins_patched.def` |
| **SHA-256 checksum** | `containers/fitlins_patched/fitlins-0.11.0_pybids-0.15.6_patched.sif.sha256` |
| **Conda environment** | `containers/fitlins_patched/environment.yml` |

Pinned versions (`containers/fitlins_patched/versions.txt`):

| Package | Version |
|---------|---------|
| fitlins | 0.11.0 |
| pybids | 0.15.6 |
| pandas | 1.5.3 |
| nipype | 1.10.0 |
| nilearn | 0.9.2 |
| matplotlib | 3.7.3 |

### Why patched

Vanilla FitLins fails on this dataset with a `NotImplementedError` on dict-valued PyBIDS entities. The patch injects a `_sanitize_pandas_query()` helper into `bids.variables.entities` that strips dict-literal comparisons from pandas query strings before `DataFrame.query()` is called.

### Rebuilding

Only rebuild if `fitlins_patched.def` or `environment.yml` changes:

```bash
cd containers/fitlins_patched/
apptainer build fitlins_patched.sif fitlins_patched.def
mv fitlins_patched.sif fitlins-0.11.0_pybids-0.15.6_patched.sif
sha256sum fitlins-0.11.0_pybids-0.15.6_patched.sif > fitlins-0.11.0_pybids-0.15.6_patched.sif.sha256
```

---

## Vanilla container (reference only)

`containers/fitlins_vanilla/` — unpatched FitLins build. Not used in the current pipeline; kept for comparison against the patched version.

---

## Host-side Python dependencies

Required on the host (outside the container) for the plotting and reporting steps:

| Package | Used by |
|---------|---------|
| matplotlib | `plot_fmri_statmaps.py`, `generate_model_report.py` |
| numpy | `plot_fmri_statmaps.py`, `generate_model_report.py` |
| pandas | `plot_fmri_statmaps.py`, `generate_model_report.py` |
| nilearn | `plot_fmri_statmaps.py` |
| Pillow | `generate_model_report.py` |
| beautifulsoup4 | `generate_model_report.py` |
| cairosvg | `generate_model_report.py` (optional; falls back if not installed) |

There is no `requirements.txt` for the host environment. Install manually or via conda/pip as needed.
