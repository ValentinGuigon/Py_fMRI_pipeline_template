#!/usr/bin/env bash
set -euo pipefail

# -------------------------
# Defaults (edit once)
# -------------------------
WORK_ROOT="/data/sld/homes/vguigon/work"
PLOTTER_PY="${WORK_ROOT}/scripts/plot_fmri_statmaps.py"

# Default FitLins outputs root (parent of node-* dirs)
FITLINS_DERIV_DEFAULT="${WORK_ROOT}/fitlins_derivatives"

# Where figures should go
FIG_PARENT_DEFAULT="${WORK_ROOT}/figures"

# -------------------------
# Usage
# -------------------------
usage() {
  cat <<EOF
Usage:
  $(basename "$0") [options]

Required:
  --out-suffix <name>
      Output folder name under ${FIG_PARENT_DEFAULT}/
      Example: tmth_visual_vs_fixation

Optional (inputs):
  --root <dir>        Derivatives root containing node-* (default: ${FITLINS_DERIV_DEFAULT})
  --nodes "<list>"    Space-separated nodes under root (default: "node-runLevel")
  --glob <pattern>    Glob pattern under each node (default: **/*stat-*.nii*)
  --subjects "<ids>"  Space-separated subject labels (no sub-) (default: none -> all)
  --tasks "<list>"    Space-separated tasks (default: none -> all)
  --runs "<list>"     Space-separated runs (default: none -> all)
  --contrasts "<list>" Space-separated contrasts (default: none -> all)
  --stats "<list>"    Space-separated stats (default: "t")

Optional (thresholding):
  --thr-mode <none|fixed|p-unc>  (default: p-unc)
  --thr-fixed <float>           (default: 3.1)
  --p-unc <float>               (default: 0.001)
  --two-sided                   (default: off)
  --df <int>                    (default: auto-infer if possible)

Optional (plotting):
  --display-mode <ortho|x|y|z>  (default: ortho)
  --cut-coords "<list>"         (default: none)
  --plot-abs                    (default: off)
  --vmax <float>                (default: none)

Other:
  --dry-run       Print python command, do not execute
  -h, --help      Show this help

Example:
  $(basename "$0") \\
    --out-suffix tmth_visual_vs_fixation \\
    --root /data/sld/homes/vguigon/work/fitlins_derivatives/tmth_visual_vs_fixation_compcor \\
    --nodes "node-runLevel" \\
    --glob "**/*stat-t*_statmap.nii*" \\
    --subjects "000 001 002 003" \\
    --thr-mode p-unc --p-unc 0.001 --two-sided \\
    --display-mode ortho --plot-abs

EOF
}

# -------------------------
# Arg parsing
# -------------------------
ROOT="${FITLINS_DERIV_DEFAULT}"
NODES_STR="node-runLevel"
GLOB="**/*stat-*.nii*"

OUT_SUFFIX=""

SUBJECTS_STR=""
TASKS_STR=""
RUNS_STR=""
CONTRASTS_STR=""
STATS_STR="t"

THR_MODE="p-unc"
THR_FIXED="3.1"
P_UNC="0.001"
TWO_SIDED="0"
DF=""

DISPLAY_MODE="ortho"
CUT_COORDS_STR=""
PLOT_ABS="0"
VMAX=""

DRY_RUN="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2;;
    --nodes) NODES_STR="${2:-}"; shift 2;;
    --glob) GLOB="${2:-}"; shift 2;;

    --out-suffix) OUT_SUFFIX="${2:-}"; shift 2;;

    --subjects) SUBJECTS_STR="${2:-}"; shift 2;;
    --tasks) TASKS_STR="${2:-}"; shift 2;;
    --runs) RUNS_STR="${2:-}"; shift 2;;
    --contrasts) CONTRASTS_STR="${2:-}"; shift 2;;
    --stats) STATS_STR="${2:-}"; shift 2;;

    --thr-mode) THR_MODE="${2:-}"; shift 2;;
    --thr-fixed) THR_FIXED="${2:-}"; shift 2;;
    --p-unc) P_UNC="${2:-}"; shift 2;;
    --two-sided) TWO_SIDED="1"; shift 1;;
    --df) DF="${2:-}"; shift 2;;

    --display-mode) DISPLAY_MODE="${2:-}"; shift 2;;
    --cut-coords) CUT_COORDS_STR="${2:-}"; shift 2;;
    --plot-abs) PLOT_ABS="1"; shift 1;;
    --vmax) VMAX="${2:-}"; shift 2;;

    --dry-run) DRY_RUN="1"; shift 1;;
    -h|--help) usage; exit 0;;
    *) echo "ERROR: unknown argument: $1" >&2; usage; exit 2;;
  esac
done

if [[ -z "${OUT_SUFFIX}" ]]; then
  echo "ERROR: --out-suffix is required" >&2
  usage
  exit 2
fi

if [[ ! -f "${PLOTTER_PY}" ]]; then
  echo "ERROR: plotter script not found: ${PLOTTER_PY}" >&2
  exit 1
fi

if [[ ! -d "${ROOT}" ]]; then
  echo "ERROR: --root not found: ${ROOT}" >&2
  exit 1
fi

OUTDIR="${FIG_PARENT_DEFAULT}/${OUT_SUFFIX}"
mkdir -p "${OUTDIR}"

# -------------------------
# Build python command
# -------------------------
# shellcheck disable=SC2206
NODES_ARR=(${NODES_STR})

CMD=(python3 "${PLOTTER_PY}"
  --root "${ROOT}"
  --nodes "${NODES_ARR[@]}"
  --glob "${GLOB}"
  --outdir "${OUTDIR}"
  --thr-mode "${THR_MODE}"
  --thr-fixed "${THR_FIXED}"
  --p-unc "${P_UNC}"
  --display-mode "${DISPLAY_MODE}"
)

if [[ "${TWO_SIDED}" == "1" ]]; then
  CMD+=(--two-sided)
fi

if [[ -n "${DF}" ]]; then
  CMD+=(--df "${DF}")
fi

if [[ -n "${SUBJECTS_STR}" ]]; then
  # shellcheck disable=SC2206
  SUBJ_ARR=(${SUBJECTS_STR})
  CMD+=(--subjects "${SUBJ_ARR[@]}")
fi

if [[ -n "${TASKS_STR}" ]]; then
  # shellcheck disable=SC2206
  TASKS_ARR=(${TASKS_STR})
  CMD+=(--tasks "${TASKS_ARR[@]}")
fi

if [[ -n "${RUNS_STR}" ]]; then
  # shellcheck disable=SC2206
  RUNS_ARR=(${RUNS_STR})
  CMD+=(--runs "${RUNS_ARR[@]}")
fi

if [[ -n "${CONTRASTS_STR}" ]]; then
  # shellcheck disable=SC2206
  CONTR_ARR=(${CONTRASTS_STR})
  CMD+=(--contrasts "${CONTR_ARR[@]}")
fi

if [[ -n "${STATS_STR}" ]]; then
  # shellcheck disable=SC2206
  STATS_ARR=(${STATS_STR})
  CMD+=(--stats "${STATS_ARR[@]}")
fi

if [[ -n "${CUT_COORDS_STR}" ]]; then
  # shellcheck disable=SC2206
  CC_ARR=(${CUT_COORDS_STR})
  CMD+=(--cut-coords "${CC_ARR[@]}")
fi

if [[ "${PLOT_ABS}" == "1" ]]; then
  CMD+=(--plot-abs)
fi

if [[ -n "${VMAX}" ]]; then
  CMD+=(--vmax "${VMAX}")
fi

# -------------------------
# Print config + run
# -------------------------
echo "== Statmap plotting runner =="
echo "Plotter:      ${PLOTTER_PY}"
echo "Root:         ${ROOT}"
echo "Nodes:        ${NODES_STR}"
echo "Glob:         ${GLOB}"
echo "Outdir:       ${OUTDIR}"
echo "Thr mode:     ${THR_MODE}"
echo "p-unc:        ${P_UNC}"
echo "two-sided:    ${TWO_SIDED}"
echo "thr-fixed:    ${THR_FIXED}"
echo "df override:  ${DF:-<auto>}"
echo

if [[ "${DRY_RUN}" == "1" ]]; then
  echo "== DRY RUN: command =="
  printf '%q ' "${CMD[@]}"
  echo
  exit 0
fi

echo "== Running =="
printf '%q ' "${CMD[@]}"
echo
echo

"${CMD[@]}"

echo
echo "== Done =="
echo "Figures:  ${OUTDIR}"
echo "Manifest: ${OUTDIR}/manifest.tsv"
