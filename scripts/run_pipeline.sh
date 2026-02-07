#!/usr/bin/env bash
set -euo pipefail

# ----------------------------------------
# Defaults (override via CLI flags)
# ----------------------------------------
WORK_ROOT="/data/sld/homes/vguigon/work"
BIDS_DIR="${WORK_ROOT}/slb_bids"

DERIV_ROOT="/data/sld/homes/collab/slb/derivatives"
DERIV_SUBDIR="fmriprep"
DERIV_LABEL="fmriprep"

SCRIPTS_DIR="${WORK_ROOT}/scripts"
MODELS_DIR="${WORK_ROOT}/fitlins_models"
OUT_PARENT="${WORK_ROOT}/fitlins_derivatives"
FIG_PARENT="${WORK_ROOT}/figures"

# Container + runner
RUN_FITLINS_SH="${SCRIPTS_DIR}/run_fitlins_models.sh"

# Tools
FIX_REPORT_PY="${SCRIPTS_DIR}/fix_fitlins_reports.py"
PLOT_STATMAPS_PY="${SCRIPTS_DIR}/plot_fmri_statmaps.py"

# Model naming convention
MODEL=""                  # e.g. tm_visual_vs_fixation_compcor
MODEL_JSON=""             # defaults to ${MODELS_DIR}/${MODEL}_smdl.json

# FitLins params
SMOOTH="6:run:iso"
SPACE="MNI152NLin2009cAsym"
NCPUS="8"
MEM_GB="16"
SUBJECTS=""               # "000 001 002 003"
DEBUG="0"

# If you want to override the derived out-suffix
OUT_SUFFIX_OVERRIDE=""

# Plot params (filters + rendering)
P_UNC="0.001"
TWO_SIDED="1"
DISPLAY_MODE="ortho"
CUT_COORDS=""             # e.g. "0 0 0" (space-separated)
PLOT_ABS="1"
VMAX=""                   # optional

CONTRASTS="visual_gt_fixation"
TASKS=""                  # optional filter list (space-separated)
RUNS=""                   # optional filter list (space-separated)
STATS="t"                 # space-separated list for --stats

# Nodes and glob
NODES_RUN="node-runLevel"
NODES_GROUP="node-subjectLevel"
PLOT_GLOB="**/*stat-t*_statmap.nii*"

# Optional explicit plot output dirs
PLOT_OUTDIR_RUN=""
PLOT_OUTDIR_GROUP=""

# Thresholding (must match plot_fmri_statmaps.py)
THR_MODE="p-unc"          # none | fixed | p-unc
THR_FIXED="3.1"           # used when THR_MODE=fixed
DF_OVERRIDE=""            # optional int; if set, passed as --df

# Control
STEPS="all"               # all | fitlins | fixreport | plot-run | plot-group
FORCE="0"
DRY_RUN="0"

# ----------------------------------------
usage() {
  cat <<EOF
Usage:
  $(basename "$0") --model-stem <stem> --subjects "<ids>" [options]

Required:
  --model-stem <stem>     Model stem (no _smdl). Example: tm_visual_vs_fixation_compcor
  --subjects "<ids>"      Space-separated participant labels. Example: "000 001 002 003"

Optional pipeline steps:
  --steps <list>          Comma-separated steps or "all".
                          Steps: fitlins,fixreport,plot-run,plot-group,all
                          Default: all

Optional paths:
  --model-json <json>     Explicit model JSON path. Default: ${MODELS_DIR}/<stem>_smdl.json
  --run-fitlins <sh>      FitLins wrapper script. Default: ${RUN_FITLINS_SH}
  --bids-dir <dir>        Default: ${BIDS_DIR}
  --models-dir <dir>      Default: ${MODELS_DIR}
  --out-parent <dir>      Default: ${OUT_PARENT}
  --fig-parent <dir>      Default: ${FIG_PARENT}

FitLins options:
  --deriv-root <dir>      Default: ${DERIV_ROOT}
  --deriv-subdir <name>   Default: ${DERIV_SUBDIR}
  --deriv-label <name>    Default: ${DERIV_LABEL}
  --out-suffix <suffix>   Override OUT_SUFFIX (default derived from model+smooth)
  --smooth <spec>         Default: ${SMOOTH}
  --space <space>         Default: ${SPACE}
  --ncpus <int>           Default: ${NCPUS}
  --mem-gb <int>          Default: ${MEM_GB}
  --debug                 Enable FitLins --debug

Plot selection options (passed to plot_fmri_statmaps.py):
  --plot-glob <glob>              Default: ${PLOT_GLOB}
  --plot-nodes-run "<nodes>"      Space-separated. Default: ${NODES_RUN}
  --plot-nodes-group "<nodes>"    Space-separated. Default: ${NODES_GROUP}
  --plot-outdir-run <dir>         Explicit output dir (run-level). Default: derived under ${FIG_PARENT}
  --plot-outdir-group <dir>       Explicit output dir (group-level). Default: derived under ${FIG_PARENT}

  --contrasts "<names>"           Space-separated. Default: ${CONTRASTS}
  --tasks "<names>"               Space-separated filter (optional)
  --runs "<ids>"                  Space-separated filter (optional)
  --stats "<names>"               Space-separated. Default: ${STATS}

Plot thresholding:
  --thr-mode <mode>               none | fixed | p-unc. Default: ${THR_MODE}
  --thr-fixed <float>             Default: ${THR_FIXED}
  --p-unc <float>                 Default: ${P_UNC}
  --df <int>                      Override df for p-unc thresholding
  --one-sided                     Use one-sided threshold (default is two-sided)

Plot rendering:
  --display-mode <mode>           ortho | x | y | z. Default: ${DISPLAY_MODE}
  --cut-coords "<nums>"           Space-separated floats (optional)
  --vmax <float>                  Optional
  --no-plot-abs                   Disable --plot-abs

Control:
  --force                 Re-run even if outputs exist
  --dry-run               Print commands without executing
  -h, --help              Show help
EOF
}

# ----------------------------------------
# Helpers
# ----------------------------------------
die() { echo "ERROR: $*" >&2; exit 1; }

run_cmd() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '[DRY RUN] '; printf '%q ' "$@"; echo
  else
    printf '[RUN] '; printf '%q ' "$@"; echo
    "$@"
  fi
}

has_step() {
  local step="$1"
  [[ "${STEPS}" == "all" ]] && return 0
  IFS=',' read -r -a _arr <<< "${STEPS}"
  for s in "${_arr[@]}"; do
    [[ "${s}" == "${step}" ]] && return 0
  done
  return 1
}

# ----------------------------------------
# Parse args
# ----------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model-stem) MODEL="${2:-}"; shift 2;;
    --subjects) SUBJECTS="${2:-}"; shift 2;;
    --steps) STEPS="${2:-}"; shift 2;;

    --model-json) MODEL_JSON="${2:-}"; shift 2;;
    --run-fitlins) RUN_FITLINS_SH="${2:-}"; shift 2;;
    --bids-dir) BIDS_DIR="${2:-}"; shift 2;;
    --models-dir) MODELS_DIR="${2:-}"; shift 2;;
    --out-parent) OUT_PARENT="${2:-}"; shift 2;;
    --fig-parent) FIG_PARENT="${2:-}"; shift 2;;

    --deriv-root) DERIV_ROOT="${2:-}"; shift 2;;
    --deriv-subdir) DERIV_SUBDIR="${2:-}"; shift 2;;
    --deriv-label) DERIV_LABEL="${2:-}"; shift 2;;
    --out-suffix) OUT_SUFFIX_OVERRIDE="${2:-}"; shift 2;;

    --smooth) SMOOTH="${2:-}"; shift 2;;
    --space) SPACE="${2:-}"; shift 2;;
    --ncpus) NCPUS="${2:-}"; shift 2;;
    --mem-gb) MEM_GB="${2:-}"; shift 2;;
    --debug) DEBUG="1"; shift 1;;

    --plot-glob) PLOT_GLOB="${2:-}"; shift 2;;
    --plot-nodes-run) NODES_RUN="${2:-}"; shift 2;;
    --plot-nodes-group) NODES_GROUP="${2:-}"; shift 2;;
    --plot-outdir-run) PLOT_OUTDIR_RUN="${2:-}"; shift 2;;
    --plot-outdir-group) PLOT_OUTDIR_GROUP="${2:-}"; shift 2;;

    --contrasts) CONTRASTS="${2:-}"; shift 2;;
    --tasks) TASKS="${2:-}"; shift 2;;
    --runs) RUNS="${2:-}"; shift 2;;
    --stats) STATS="${2:-}"; shift 2;;

    --thr-mode) THR_MODE="${2:-}"; shift 2;;
    --thr-fixed) THR_FIXED="${2:-}"; shift 2;;
    --p-unc) P_UNC="${2:-}"; shift 2;;
    --df) DF_OVERRIDE="${2:-}"; shift 2;;
    --one-sided) TWO_SIDED="0"; shift 1;;

    --display-mode) DISPLAY_MODE="${2:-}"; shift 2;;
    --cut-coords) CUT_COORDS="${2:-}"; shift 2;;
    --vmax) VMAX="${2:-}"; shift 2;;
    --no-plot-abs) PLOT_ABS="0"; shift 1;;

    --force) FORCE="1"; shift 1;;
    --dry-run) DRY_RUN="1"; shift 1;;
    -h|--help) usage; exit 0;;
    *) die "Unknown argument: $1";;
  esac
done

[[ -n "${MODEL}" ]] || die "--model-stem is required"
[[ -n "${SUBJECTS}" ]] || die "--subjects is required"

# Default model json path
if [[ -z "${MODEL_JSON}" ]]; then
  MODEL_JSON="${MODELS_DIR}/${MODEL}_smdl.json"
fi
[[ -f "${MODEL_JSON}" ]] || die "model JSON not found: ${MODEL_JSON}"

# Derive suffix from SMOOTH unless overridden
KERNEL_MM="$(echo "${SMOOTH}" | cut -d: -f1)"
OUT_SUFFIX="${MODEL}_s${KERNEL_MM}"
if [[ -n "${OUT_SUFFIX_OVERRIDE}" ]]; then
  OUT_SUFFIX="${OUT_SUFFIX_OVERRIDE}"
fi

OUT_DIR="${OUT_PARENT}/${OUT_SUFFIX}"
WORK_DIR="${WORK_ROOT}/work_fitlins/work_fitlins_tmp_${OUT_SUFFIX}"

REPORT_GLOB_PATTERN="${OUT_DIR}/reports/model-*.html"

# Flags for plot_fmri_statmaps.py
PLOT_ABS_FLAG=()
[[ "${PLOT_ABS}" == "1" ]] && PLOT_ABS_FLAG=(--plot-abs)

TWO_SIDED_FLAG=()
[[ "${TWO_SIDED}" == "1" ]] && TWO_SIDED_FLAG=(--two-sided)

THR_ARGS=()
case "${THR_MODE}" in
  none)  THR_ARGS=(--thr-mode none) ;;
  fixed) THR_ARGS=(--thr-mode fixed --thr-fixed "${THR_FIXED}") ;;
  p-unc) THR_ARGS=(--thr-mode p-unc --p-unc "${P_UNC}") ;;
  *) die "Unknown --thr-mode: ${THR_MODE} (allowed: none,fixed,p-unc)" ;;
esac

DF_FLAG=()
[[ -n "${DF_OVERRIDE}" ]] && DF_FLAG=(--df "${DF_OVERRIDE}")

TASKS_FLAG=()
[[ -n "${TASKS}" ]] && TASKS_FLAG=(--tasks ${TASKS})

RUNS_FLAG=()
[[ -n "${RUNS}" ]] && RUNS_FLAG=(--runs ${RUNS})

STATS_FLAG=()
[[ -n "${STATS}" ]] && STATS_FLAG=(--stats ${STATS})

CUT_COORDS_FLAG=()
[[ -n "${CUT_COORDS}" ]] && CUT_COORDS_FLAG=(--cut-coords ${CUT_COORDS})

VMAX_FLAG=()
[[ -n "${VMAX}" ]] && VMAX_FLAG=(--vmax "${VMAX}")

# ----------------------------------------
# Print config
# ----------------------------------------
cat <<EOF
== Pipeline config ==
MODEL:        ${MODEL}
MODEL_JSON:   ${MODEL_JSON}
SUBJECTS:     ${SUBJECTS}
BIDS_DIR:     ${BIDS_DIR}

DERIV_ROOT:   ${DERIV_ROOT}
DERIV_SUBDIR: ${DERIV_SUBDIR}
DERIV_LABEL:  ${DERIV_LABEL}

SMOOTH:       ${SMOOTH}
SPACE:        ${SPACE}
NCPUS:        ${NCPUS}
MEM_GB:       ${MEM_GB}
DEBUG:        ${DEBUG}

OUT_SUFFIX:   ${OUT_SUFFIX}
OUT_DIR:      ${OUT_DIR}
WORK_DIR:     ${WORK_DIR}

PLOT_GLOB:    ${PLOT_GLOB}
NODES_RUN:    ${NODES_RUN}
NODES_GROUP:  ${NODES_GROUP}

CONTRASTS:    ${CONTRASTS}
TASKS:        ${TASKS}
RUNS:         ${RUNS}
STATS:        ${STATS}

THR_MODE:     ${THR_MODE}
THR_FIXED:    ${THR_FIXED}
P_UNC:        ${P_UNC}
TWO_SIDED:    ${TWO_SIDED}
DF_OVERRIDE:  ${DF_OVERRIDE}

DISPLAY_MODE: ${DISPLAY_MODE}
CUT_COORDS:   ${CUT_COORDS}
PLOT_ABS:     ${PLOT_ABS}
VMAX:         ${VMAX}

STEPS:        ${STEPS}
FORCE:        ${FORCE}
DRY_RUN:      ${DRY_RUN}
EOF
echo

# ----------------------------------------
# Step: Run FitLins
# ----------------------------------------
if has_step "fitlins"; then
  [[ -x "${RUN_FITLINS_SH}" ]] || die "FitLins runner not executable: ${RUN_FITLINS_SH}"
  [[ -d "${BIDS_DIR}" ]] || die "BIDS dir not found: ${BIDS_DIR}"

  if [[ -d "${OUT_DIR}" && "${FORCE}" != "1" ]]; then
    cat <<EOF
[SKIP:fitlins] Output directory already exists:
  ${OUT_DIR}

FitLins will NOT be re-run to avoid overwriting results.

If you intended to re-run the analysis and overwrite existing outputs, re-run with:
  --force

If you intended to only plot existing results, remove 'fitlins' from --steps.
EOF
  # IMPORTANT: stop here if fitlins was explicitly requested
  [[ "${STEPS}" == "all" || "${STEPS}" == *"fitlins"* ]] && exit 1

  else
    mkdir -p "${OUT_DIR}" "$(dirname "${WORK_DIR}")" "${WORK_DIR}"

    FITLINS_ARGS=(
      "${RUN_FITLINS_SH}"
      --bids-dir "${BIDS_DIR}"
      --model "${MODEL_JSON}"
      --subjects "${SUBJECTS}"
      --deriv-root "${DERIV_ROOT}"
      --deriv-subdir "${DERIV_SUBDIR}"
      --deriv-label "${DERIV_LABEL}"
      --out-parent "${OUT_PARENT}"
      --out-suffix "${OUT_SUFFIX}"
      --smooth "${SMOOTH}"
      --space "${SPACE}"
      --ncpus "${NCPUS}"
      --mem-gb "${MEM_GB}"
    )
    [[ "${DEBUG}" == "1" ]] && FITLINS_ARGS+=(--debug)

    run_cmd "${FITLINS_ARGS[@]}"
  fi
fi

# ----------------------------------------
# Step: Fix report (best-effort)
# ----------------------------------------
if has_step "fixreport"; then
  [[ -f "${FIX_REPORT_PY}" ]] || die "fix_fitlins_reports.py not found: ${FIX_REPORT_PY}"

  REPORT_FILE="$(ls -1 ${REPORT_GLOB_PATTERN} 2>/dev/null | head -n 1 || true)"
  if [[ -z "${REPORT_FILE}" ]]; then
    echo "[SKIP:fixreport] No report found under: ${OUT_DIR}/reports/"
  else
    if [[ "${FORCE}" != "1" && -f "${REPORT_FILE}.fixed" ]]; then
      echo "[SKIP:fixreport] Looks already fixed: ${REPORT_FILE}.fixed (use --force to rerun)"
    fi
    run_cmd python3 "${FIX_REPORT_PY}" "${REPORT_FILE}" --verbose
  fi
fi

# ----------------------------------------
# Step: Plot run-level maps
# ----------------------------------------
if has_step "plot-run"; then
  [[ -f "${PLOT_STATMAPS_PY}" ]] || die "plot_fmri_statmaps.py not found: ${PLOT_STATMAPS_PY}"

  if [[ -n "${PLOT_OUTDIR_RUN}" ]]; then
    OUTFIG_RUN="${PLOT_OUTDIR_RUN}"
  else
    OUTFIG_RUN="${FIG_PARENT}/${OUT_SUFFIX}_run_${THR_MODE}"
    [[ "${THR_MODE}" == "p-unc" ]] && OUTFIG_RUN+="_p${P_UNC}"
    [[ "${THR_MODE}" == "fixed" ]] && OUTFIG_RUN+="_t${THR_FIXED}"
    [[ -n "${DF_OVERRIDE}" ]] && OUTFIG_RUN+="_df${DF_OVERRIDE}"
  fi

  run_cmd python3 "${PLOT_STATMAPS_PY}" \
    --root "${OUT_DIR}" \
    --nodes ${NODES_RUN} \
    --glob "${PLOT_GLOB}" \
    --outdir "${OUTFIG_RUN}" \
    --subjects ${SUBJECTS} \
    --contrasts ${CONTRASTS} \
    "${TASKS_FLAG[@]}" \
    "${RUNS_FLAG[@]}" \
    "${STATS_FLAG[@]}" \
    "${THR_ARGS[@]}" \
    "${TWO_SIDED_FLAG[@]}" \
    "${DF_FLAG[@]}" \
    --display-mode "${DISPLAY_MODE}" \
    "${CUT_COORDS_FLAG[@]}" \
    "${PLOT_ABS_FLAG[@]}" \
    "${VMAX_FLAG[@]}"
fi

# ----------------------------------------
# Step: Plot subject/group-level maps
# ----------------------------------------
if has_step "plot-group"; then
  [[ -f "${PLOT_STATMAPS_PY}" ]] || die "plot_fmri_statmaps.py not found: ${PLOT_STATMAPS_PY}"

  if [[ -n "${PLOT_OUTDIR_GROUP}" ]]; then
    OUTFIG_GROUP="${PLOT_OUTDIR_GROUP}"
  else
    OUTFIG_GROUP="${FIG_PARENT}/${OUT_SUFFIX}_group_${THR_MODE}"
    [[ "${THR_MODE}" == "p-unc" ]] && OUTFIG_GROUP+="_p${P_UNC}"
    [[ "${THR_MODE}" == "fixed" ]] && OUTFIG_GROUP+="_t${THR_FIXED}"
    [[ -n "${DF_OVERRIDE}" ]] && OUTFIG_GROUP+="_df${DF_OVERRIDE}"
  fi

  run_cmd python3 "${PLOT_STATMAPS_PY}" \
    --root "${OUT_DIR}" \
    --nodes ${NODES_GROUP} \
    --glob "${PLOT_GLOB}" \
    --outdir "${OUTFIG_GROUP}" \
    --contrasts ${CONTRASTS} \
    "${TASKS_FLAG[@]}" \
    "${RUNS_FLAG[@]}" \
    "${STATS_FLAG[@]}" \
    "${THR_ARGS[@]}" \
    "${TWO_SIDED_FLAG[@]}" \
    "${DF_FLAG[@]}" \
    --display-mode "${DISPLAY_MODE}" \
    "${CUT_COORDS_FLAG[@]}" \
    "${PLOT_ABS_FLAG[@]}" \
    "${VMAX_FLAG[@]}"
fi

echo
echo "== DONE =="
echo "Outputs: ${OUT_DIR}"
echo "Figures: ${FIG_PARENT}/${OUT_SUFFIX}_*"
