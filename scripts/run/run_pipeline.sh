#!/usr/bin/env bash
set -euo pipefail

# ----------------------------------------
# Defaults (override via --config or CLI flags)
# ----------------------------------------
WORK_ROOT="/data/sld/homes/vguigon/slb_work"
BIDS_DIR="${WORK_ROOT}/slb_bids_runs"

DERIV_ROOT="/data/sld/homes/collab/slb/derivatives"
DERIV_SUBDIR="fmriprep"
DERIV_LABEL="fmriprep"

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="${WORK_ROOT}/fitlins_models"
OUT_PARENT="${WORK_ROOT}/fitlins_derivatives"
FIG_PARENT="${WORK_ROOT}/figures"
REPORTS_PARENT="${WORK_ROOT}/reports"

# Task group (optional): if set, all output dirs are namespaced under a subdir.
# Set in your config file to group models by task/study (e.g. TASK_GROUP="tmth").
# Results in: fitlins_derivatives/tmth/, figures/tmth/, fitlins_models/tmth/
TASK_GROUP=""

# Container + runner
RUN_FITLINS_SH="${SCRIPTS_DIR}/run_fitlins_models.sh"

# Tools
FIX_REPORT_PY="${SCRIPTS_DIR}/fix_fitlins_reports.py"
PLOT_STATMAPS_PY="${SCRIPTS_DIR}/plot_fmri_statmaps.py"
PARSE_MODEL_PY="${SCRIPTS_DIR}/parse_model.py"
REPORT_PY="${SCRIPTS_DIR}/../report/generate_model_report.py"

# Model
MODEL=""        # e.g. tm_visual_vs_fixation_compcor  (stem only, no _smdl.json)
MODEL_JSON=""   # defaults to ${MODELS_DIR}/${MODEL}_smdl.json

# FitLins params
SMOOTH="6:run:iso"
SPACE="MNI152NLin2009cAsym"
NCPUS="8"
MEM_GB="16"
SUBJECTS=""     # auto-derived from model JSON if empty
DEBUG="0"

# Out-suffix override
OUT_SUFFIX_OVERRIDE=""

# Events override directory (optional).
# Points to a subdirectory of slb_events/ (e.g. slb_events/motor).
# If set, events TSVs that differ from those currently in BIDS_DIR are
# copied in before FitLins runs.  Unset for standard (visual) models.
EVENTS_DIR=""

# Plot filters
# All of these default to "" = auto-derive from model JSON at resolution time
CONTRASTS=""    # auto-derived from model JSON if empty
STATS=""        # auto-derived from model JSON if empty (t or z)
TASKS=""        # optional filter; if empty, no --tasks flag is passed
RUNS=""         # optional filter; if empty, no --runs flag is passed

# Nodes
# NODES_RUN: auto-derived from model JSON if empty
# GROUP_NODES: auto-derived from model JSON if empty (all non-Run nodes, bare names)
NODES_RUN=""
GROUP_NODES=""

# Plot glob: auto-set from STATS after resolution if not explicitly provided
PLOT_GLOB=""
PLOT_GLOB_EXPLICIT="0"   # set to 1 when --plot-glob is passed on CLI or in config

# Optional explicit plot output dirs (overrides auto-derived paths)
PLOT_OUTDIR_RUN=""
PLOT_OUTDIR_GROUP=""     # if multiple group nodes, node name is appended automatically

# Thresholding
P_UNC="0.001"
TWO_SIDED="1"
THR_MODE="p-unc"    # none | fixed | p-unc | fdr | bonferroni | ari
THR_FIXED="3.1"
ALPHA="0.05"        # FDR/FWER/ARI control level
ARI_THRESHOLDS=""   # space-separated z-thresholds for ari (default: "2.5 3.0 3.5")
DF_OVERRIDE=""
ROI_MASK=""         # path to NIfTI mask for SVC; empty = whole-brain
CLUSTER_TABLE="0"   # 1 = write TSV cluster summary alongside figures

# Plot rendering
DISPLAY_MODE="ortho"
CUT_COORDS=""
PLOT_ABS="1"
VMAX=""
CLUSTER_EXTENT=""   # minimum cluster size in voxels; empty = off
VIEW3D="0"          # set to 1 to generate _3d.html interactive viewers

# Control
STEPS="all"   # all | fitlins | fixreport | plot-run | plot-group | report  (comma-separated)
FORCE="0"
DRY_RUN="0"

# Reporting
REPORT_OUTPUT=""
REPORT_THR_SUFFIX=""

# ----------------------------------------
# Config file loading
# Must come before arg parsing so CLI flags can still override config values.
# Usage:  run_pipeline.sh --config path/to/analysis.cfg [other flags...]
# ----------------------------------------
if [[ "${1:-}" == "--config" ]]; then
  _CONFIG_FILE="${2:-}"
  [[ -f "${_CONFIG_FILE}" ]] || { echo "ERROR: config file not found: ${_CONFIG_FILE}" >&2; exit 1; }
  # shellcheck source=/dev/null
  source "${_CONFIG_FILE}"
  echo "[config] Loaded: ${_CONFIG_FILE}"
  shift 2
fi

# ----------------------------------------
usage() {
  cat <<EOF
Usage:
  $(basename "$0") [--config <cfg>] --model-stem <stem> [options]

Config file (optional, loaded before CLI args):
  --config <cfg>          Source a bash config file. CLI flags override config values.
                          Example config:
                            MODEL="tm_visual_vs_fixation_compcor"
                            SUBJECTS="000 001 002 003"
                            BIDS_DIR="/path/to/bids"
                            SMOOTH="6:run:iso"
                            THR_MODE="p-unc"
                            P_UNC="0.001"

Required (unless set in config):
  --model-stem <stem>     Model JSON stem (no _smdl suffix).
                          Example: tm_visual_vs_fixation_compcor
                          Expects: \${MODELS_DIR}/<stem>_smdl.json

Optional - model / paths:
  --model-json <json>     Explicit model JSON path (bypasses stem convention)
  --bids-dir <dir>        Default: ${BIDS_DIR}
  --models-dir <dir>      Default: ${MODELS_DIR}
  --out-parent <dir>      Default: ${OUT_PARENT}
  --fig-parent <dir>      Default: ${FIG_PARENT}
  --reports-parent <dir>  Default: ${REPORTS_PARENT}
  --task-group <name>     Namespace all outputs under a subdir (e.g. "tmth").
                          Sets fitlins_derivatives/<name>/, figures/<name>/, fitlins_models/<name>/
                          Typically set in the config file as TASK_GROUP="tmth".
  --run-fitlins <sh>      FitLins wrapper script. Default: ${RUN_FITLINS_SH}

Optional - FitLins:
  --deriv-root <dir>      Default: ${DERIV_ROOT}
  --deriv-subdir <name>   Default: ${DERIV_SUBDIR}
  --deriv-label <name>    Default: ${DERIV_LABEL}
  --out-suffix <suffix>   Override derived output suffix (<stem>_s<kernel>)
  --subjects "<ids>"      Space-separated labels. Default: auto from model JSON
  --smooth <spec>         Default: ${SMOOTH}
  --space <space>         Default: ${SPACE}
  --ncpus <int>           Default: ${NCPUS}
  --mem-gb <int>          Default: ${MEM_GB}
  --debug                 Enable FitLins --debug

Optional - plot filters (all auto-derived from model JSON if omitted):
  --contrasts "<names>"   Space-separated. Default: all contrasts in model JSON
  --stats "<names>"       Space-separated. Default: test type from model JSON (t or z)
  --tasks "<names>"       Space-separated task filter (optional, no default filter)
  --runs "<ids>"          Space-separated run filter (optional, no default filter)
  --plot-glob <glob>      Default: auto from stats type, e.g. **/*stat-t*_statmap.nii*
  --plot-nodes-run "<n>"  Space-separated run-level node(s). Default: auto from model JSON
  --plot-nodes-group "<n>" Space-separated group node(s). Default: auto from model JSON
                           (all non-Run nodes; pipeline loops over each automatically)
  --plot-outdir-run <dir>  Explicit run-level figure dir
  --plot-outdir-group <dir> Explicit group-level figure dir
                            (node name appended automatically when multiple group nodes)
  --report-output <pdf>    Output PDF path for the report step
  --report-thr-suffix <s>  Threshold suffix passed to generate_model_report.py
                           Default: auto-derived from threshold settings

Optional - thresholding:
  --thr-mode <mode>       none | fixed | p-unc | fdr | bonferroni | ari. Default: ${THR_MODE}
  --thr-fixed <float>     Used when thr-mode=fixed. Default: ${THR_FIXED}
  --p-unc <float>         Used when thr-mode=p-unc. Default: ${P_UNC}
  --alpha <float>         FDR/FWER/ARI control level. Default: ${ALPHA}
  --ari-thresholds "<z>"  Space-separated cluster-forming z-thresholds for ari. Default: "2.5 3.0 3.5"
  --df <int>              Override df for p-unc (default: auto-infer from design matrix)
  --one-sided             One-sided threshold (default is two-sided)
  --roi-mask <path>       NIfTI mask for SVC (restricts correction to mask voxels)
  --cluster-table         Write TSV cluster summary alongside figures

Optional - rendering:
  --display-mode <mode>   ortho | x | y | z. Default: ${DISPLAY_MODE}
  --cut-coords "<nums>"   Space-separated floats (optional)
  --vmax <float>          Optional colorbar max
  --no-plot-abs           Disable absolute-value plotting

Control:
  --steps <list>          Comma-separated: fitlins,fixreport,plot-run,plot-group,report,all
                          Default: all
  --force                 Re-run even if outputs already exist
  --dry-run               Print commands without executing
  -h, --help              Show this help

Examples:
  # Minimal - everything auto-derived from model JSON:
  $(basename "$0") --model-stem tm_visual_vs_fixation_compcor

  # With config file:
  $(basename "$0") --config configs/tmth.cfg --steps plot-run,plot-group,report

  # Override threshold only:
  $(basename "$0") --model-stem tmth_visual_vs_fixation_compcor --thr-mode fixed --thr-fixed 4.0
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

parse_model_field() {
  # parse_model_field <field>  -> stdout
  local field="$1"
  python3 "${PARSE_MODEL_PY}" "${MODEL_JSON}" --field "${field}"
}

build_thr_suffix() {
  local suffix="${THR_MODE}"
  [[ "${THR_MODE}" == "p-unc" ]] && suffix+="_p${P_UNC}"
  [[ "${THR_MODE}" == "fixed" ]] && suffix+="_t${THR_FIXED}"
  [[ "${THR_MODE}" == "fdr" || "${THR_MODE}" == "bonferroni" || "${THR_MODE}" == "ari" ]] && suffix+="_a${ALPHA}"
  [[ "${TWO_SIDED}" == "1" ]] && suffix+="_2s" || suffix+="_1s"
  [[ -n "${DF_OVERRIDE}" ]] && suffix+="_df${DF_OVERRIDE}"
  [[ -n "${ROI_MASK}" ]] && suffix+="_svc"
  echo "${suffix}"
}

# ----------------------------------------
# Parse CLI args
# ----------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model-stem)        MODEL="${2:-}"; shift 2;;
    --subjects)          SUBJECTS="${2:-}"; shift 2;;
    --steps)             STEPS="${2:-}"; shift 2;;

    --model-json)        MODEL_JSON="${2:-}"; shift 2;;
    --run-fitlins)       RUN_FITLINS_SH="${2:-}"; shift 2;;
    --bids-dir)          BIDS_DIR="${2:-}"; shift 2;;
    --models-dir)        MODELS_DIR="${2:-}"; shift 2;;
    --out-parent)        OUT_PARENT="${2:-}"; shift 2;;
    --fig-parent)        FIG_PARENT="${2:-}"; shift 2;;
    --reports-parent)    REPORTS_PARENT="${2:-}"; shift 2;;
    --task-group)        TASK_GROUP="${2:-}"; shift 2;;

    --deriv-root)        DERIV_ROOT="${2:-}"; shift 2;;
    --deriv-subdir)      DERIV_SUBDIR="${2:-}"; shift 2;;
    --deriv-label)       DERIV_LABEL="${2:-}"; shift 2;;
    --out-suffix)        OUT_SUFFIX_OVERRIDE="${2:-}"; shift 2;;
    --events-dir)        EVENTS_DIR="${2:-}"; shift 2;;

    --smooth)            SMOOTH="${2:-}"; shift 2;;
    --space)             SPACE="${2:-}"; shift 2;;
    --ncpus)             NCPUS="${2:-}"; shift 2;;
    --mem-gb)            MEM_GB="${2:-}"; shift 2;;
    --debug)             DEBUG="1"; shift 1;;

    --plot-glob)         PLOT_GLOB="${2:-}"; PLOT_GLOB_EXPLICIT="1"; shift 2;;
    --plot-nodes-run)    NODES_RUN="${2:-}"; shift 2;;
    --plot-nodes-group)  GROUP_NODES="${2:-}"; shift 2;;
    --plot-outdir-run)   PLOT_OUTDIR_RUN="${2:-}"; shift 2;;
    --plot-outdir-group) PLOT_OUTDIR_GROUP="${2:-}"; shift 2;;
    --report-output)     REPORT_OUTPUT="${2:-}"; shift 2;;
    --report-thr-suffix) REPORT_THR_SUFFIX="${2:-}"; shift 2;;

    --contrasts)         CONTRASTS="${2:-}"; shift 2;;
    --tasks)             TASKS="${2:-}"; shift 2;;
    --runs)              RUNS="${2:-}"; shift 2;;
    --stats)             STATS="${2:-}"; shift 2;;

    --thr-mode)          THR_MODE="${2:-}"; shift 2;;
    --thr-fixed)         THR_FIXED="${2:-}"; shift 2;;
    --p-unc)             P_UNC="${2:-}"; shift 2;;
    --alpha)             ALPHA="${2:-}"; shift 2;;
    --ari-thresholds)    ARI_THRESHOLDS="${2:-}"; shift 2;;
    --df)                DF_OVERRIDE="${2:-}"; shift 2;;
    --one-sided)         TWO_SIDED="0"; shift 1;;
    --roi-mask)          ROI_MASK="${2:-}"; shift 2;;
    --cluster-table)     CLUSTER_TABLE="1"; shift 1;;

    --display-mode)      DISPLAY_MODE="${2:-}"; shift 2;;
    --cut-coords)        CUT_COORDS="${2:-}"; shift 2;;
    --vmax)              VMAX="${2:-}"; shift 2;;
    --cluster-extent)    CLUSTER_EXTENT="${2:-}"; shift 2;;
    --view3d)            VIEW3D="1"; shift 1;;
    --no-plot-abs)       PLOT_ABS="0"; shift 1;;

    --force)             FORCE="1"; shift 1;;
    --dry-run)           DRY_RUN="1"; shift 1;;
    -h|--help)           usage; exit 0;;
    *)                   die "Unknown argument: $1";;
  esac
done

# ----------------------------------------
# Apply TASK_GROUP namespacing
# If TASK_GROUP is set (via config or --task-group), append it to the three
# output roots so all artifacts land under a group-specific subfolder.
# ----------------------------------------
if [[ -n "${TASK_GROUP}" ]]; then
  OUT_PARENT="${OUT_PARENT%/}/${TASK_GROUP}"
  FIG_PARENT="${FIG_PARENT%/}/${TASK_GROUP}"
  MODELS_DIR="${MODELS_DIR%/}/${TASK_GROUP}"
  REPORTS_PARENT="${REPORTS_PARENT%/}/${TASK_GROUP}"
fi

# ----------------------------------------
# Validate and resolve MODEL_JSON
# ----------------------------------------
[[ -n "${MODEL}" ]] || die "--model-stem is required (or set MODEL= in your config)"
[[ -f "${PARSE_MODEL_PY}" ]] || die "parse_model.py not found: ${PARSE_MODEL_PY}"

if [[ -z "${MODEL_JSON}" ]]; then
  MODEL_JSON="${MODELS_DIR}/${MODEL}_smdl.json"
fi
[[ -f "${MODEL_JSON}" ]] || die "Model JSON not found: ${MODEL_JSON}
  Tried: ${MODEL_JSON}
  Tip: if your file is named runs_${MODEL}_smdl.json, pass --model-json explicitly
       or rename the file to ${MODEL}_smdl.json"

# ----------------------------------------
# Auto-derive fields from model JSON
# Anything explicitly set on CLI / config takes priority.
# ----------------------------------------
echo "[auto] Parsing model JSON: ${MODEL_JSON}"

if [[ -z "${SUBJECTS}" ]]; then
  SUBJECTS="$(parse_model_field subjects)"
  echo "[auto] SUBJECTS:     ${SUBJECTS}"
fi

if [[ -z "${CONTRASTS}" ]]; then
  CONTRASTS="$(parse_model_field contrasts)"
  echo "[auto] CONTRASTS:    ${CONTRASTS}"
fi

if [[ -z "${STATS}" ]]; then
  STATS="$(parse_model_field stat)"
  echo "[auto] STATS:        ${STATS}"
fi

if [[ -z "${NODES_RUN}" ]]; then
  _RUN_NODE="$(parse_model_field run_node)"
  NODES_RUN="node-${_RUN_NODE}"
  echo "[auto] NODES_RUN:    ${NODES_RUN}"
fi

if [[ -z "${GROUP_NODES}" ]]; then
  GROUP_NODES="$(parse_model_field group_nodes)"
  echo "[auto] GROUP_NODES:  ${GROUP_NODES}"
fi

# Auto-set plot glob from stat type (unless user provided --plot-glob)
if [[ "${PLOT_GLOB_EXPLICIT}" == "0" ]]; then
  PLOT_GLOB="**/*stat-${STATS}*_statmap.nii*"
  echo "[auto] PLOT_GLOB:    ${PLOT_GLOB}"
fi

# Validate we have subjects
[[ -n "${SUBJECTS}" ]] || die "No subjects available: set --subjects or add 'subject' list to model JSON Input"

# Validate we have contrasts
[[ -n "${CONTRASTS}" ]] || die "No contrasts found in model JSON and --contrasts not provided"

# ----------------------------------------
# Derive output suffix and paths
# ----------------------------------------
KERNEL_MM="$(echo "${SMOOTH}" | cut -d: -f1)"
OUT_SUFFIX="${MODEL}_s${KERNEL_MM}"
[[ -n "${OUT_SUFFIX_OVERRIDE}" ]] && OUT_SUFFIX="${OUT_SUFFIX_OVERRIDE}"

OUT_DIR="${OUT_PARENT}/${OUT_SUFFIX}"
WORK_DIR="${WORK_ROOT}/work_fitlins/work_fitlins_tmp_${OUT_SUFFIX}"
REPORT_GLOB_PATTERN="${OUT_DIR}/reports/model-*.html"
AUTO_REPORT_THR_SUFFIX="$(build_thr_suffix)"
[[ -n "${REPORT_THR_SUFFIX}" ]] || REPORT_THR_SUFFIX="${AUTO_REPORT_THR_SUFFIX}"
[[ -n "${REPORT_OUTPUT}" ]] || REPORT_OUTPUT="${REPORTS_PARENT}/${OUT_SUFFIX}__${REPORT_THR_SUFFIX}.pdf"

# ----------------------------------------
# Build flag arrays for plot_fmri_statmaps.py
# ----------------------------------------
PLOT_ABS_FLAG=()
[[ "${PLOT_ABS}" == "1" ]] && PLOT_ABS_FLAG=(--plot-abs)

TWO_SIDED_FLAG=()
[[ "${TWO_SIDED}" == "1" ]] && TWO_SIDED_FLAG=(--two-sided)

THR_ARGS=()
case "${THR_MODE}" in
  none)        THR_ARGS=(--thr-mode none) ;;
  fixed)       THR_ARGS=(--thr-mode fixed --thr-fixed "${THR_FIXED}") ;;
  p-unc)       THR_ARGS=(--thr-mode p-unc --p-unc "${P_UNC}") ;;
  fdr)         THR_ARGS=(--thr-mode fdr --alpha "${ALPHA}") ;;
  bonferroni)  THR_ARGS=(--thr-mode bonferroni --alpha "${ALPHA}") ;;
  ari)         THR_ARGS=(--thr-mode ari --alpha "${ALPHA}")
               [[ -n "${ARI_THRESHOLDS}" ]] && THR_ARGS+=(--ari-thresholds ${ARI_THRESHOLDS}) ;;
  *)           die "Unknown --thr-mode: ${THR_MODE} (allowed: none, fixed, p-unc, fdr, bonferroni, ari)" ;;
esac

ROI_MASK_FLAG=()
[[ -n "${ROI_MASK}" ]] && ROI_MASK_FLAG=(--roi-mask "${ROI_MASK}")

CLUSTER_TABLE_FLAG=()
[[ "${CLUSTER_TABLE}" == "1" ]] && CLUSTER_TABLE_FLAG=(--cluster-table)

DF_FLAG=()
[[ -n "${DF_OVERRIDE}" ]] && DF_FLAG=(--df "${DF_OVERRIDE}")

TASKS_FLAG=()
[[ -n "${TASKS}" ]] && TASKS_FLAG=(--tasks ${TASKS})

RUNS_FLAG=()
[[ -n "${RUNS}" ]] && RUNS_FLAG=(--runs ${RUNS})

# Always pass --stats explicitly (auto-derived or user-set)
STATS_FLAG=(--stats ${STATS})

CUT_COORDS_FLAG=()
[[ -n "${CUT_COORDS}" ]] && CUT_COORDS_FLAG=(--cut-coords ${CUT_COORDS})

VMAX_FLAG=()
[[ -n "${VMAX}" ]] && VMAX_FLAG=(--vmax "${VMAX}")

CLUSTER_EXTENT_FLAG=()
[[ -n "${CLUSTER_EXTENT}" ]] && CLUSTER_EXTENT_FLAG=(--cluster-extent "${CLUSTER_EXTENT}")

VIEW3D_FLAG=()
[[ "${VIEW3D}" == "1" ]] && VIEW3D_FLAG=(--view3d)

# ----------------------------------------
# Print resolved config
# ----------------------------------------
cat <<EOF

== Pipeline config ==
MODEL:        ${MODEL}
TASK_GROUP:   ${TASK_GROUP:-<none>}
MODEL_JSON:   ${MODEL_JSON}
SUBJECTS:     ${SUBJECTS}
BIDS_DIR:     ${BIDS_DIR}
EVENTS_DIR:   ${EVENTS_DIR:-<none>}

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
GROUP_NODES:  ${GROUP_NODES}
REPORT_OUTPUT: ${REPORT_OUTPUT}
REPORT_THR_SUFFIX: ${REPORT_THR_SUFFIX}

CONTRASTS:    ${CONTRASTS}
TASKS:        ${TASKS:-<all>}
RUNS:         ${RUNS:-<all>}
STATS:        ${STATS}

THR_MODE:     ${THR_MODE}
THR_FIXED:    ${THR_FIXED}
P_UNC:        ${P_UNC}
ALPHA:        ${ALPHA}
ARI_THRESHOLDS: ${ARI_THRESHOLDS:-<default>}
TWO_SIDED:    ${TWO_SIDED}
DF_OVERRIDE:  ${DF_OVERRIDE:-<auto>}
ROI_MASK:     ${ROI_MASK:-<none>}

DISPLAY_MODE: ${DISPLAY_MODE}
CUT_COORDS:   ${CUT_COORDS:-<none>}
PLOT_ABS:     ${PLOT_ABS}
VMAX:         ${VMAX:-<none>}
CLUSTER_EXTENT: ${CLUSTER_EXTENT:-<off>}
CLUSTER_TABLE: ${CLUSTER_TABLE}
VIEW3D:       ${VIEW3D}

STEPS:        ${STEPS}
FORCE:        ${FORCE}
DRY_RUN:      ${DRY_RUN}
EOF
echo

# ----------------------------------------
# Step: fitlins
# ----------------------------------------
if has_step "fitlins"; then
  [[ -x "${RUN_FITLINS_SH}" ]] || die "FitLins runner not executable: ${RUN_FITLINS_SH}"
  [[ -d "${BIDS_DIR}" ]] || die "BIDS dir not found: ${BIDS_DIR}"

  if [[ -d "${OUT_DIR}" && "${FORCE}" != "1" ]]; then
    cat <<EOF
[SKIP:fitlins] Output directory already exists:
  ${OUT_DIR}

FitLins will NOT be re-run to avoid overwriting results.
Re-run with --force to overwrite, or use --steps plot-run,plot-group to only plot.
EOF
    [[ "${STEPS}" == "all" || "${STEPS}" == *"fitlins"* ]] && exit 1
  else
    mkdir -p "${OUT_DIR}" "$(dirname "${WORK_DIR}")" "${WORK_DIR}"

    # ---- Events override (diff-then-copy) ----
    if [[ -n "${EVENTS_DIR}" ]]; then
      [[ -d "${EVENTS_DIR}" ]] || die "EVENTS_DIR not found: ${EVENTS_DIR}"
      echo "[events-override] Syncing events TSVs from ${EVENTS_DIR} ..."
      n_copied=0
      n_same=0
      while IFS= read -r -d '' src_tsv; do
        rel="${src_tsv#${EVENTS_DIR}/}"
        dst_tsv="${BIDS_DIR}/${rel}"
        if [[ ! -f "${dst_tsv}" ]]; then
          echo "  [WARN] No matching file in BIDS_DIR: ${rel}"
          continue
        fi
        if cmp -s "${src_tsv}" "${dst_tsv}"; then
          (( n_same++ )) || true
        else
          cp "${src_tsv}" "${dst_tsv}"
          echo "  [COPY] ${rel}"
          (( n_copied++ )) || true
        fi
      done < <(find "${EVENTS_DIR}" -name "*_events.tsv" -print0)
      echo "[events-override] Done. Copied: ${n_copied}  Already up-to-date: ${n_same}"
    fi

    FITLINS_ARGS=(
      "${RUN_FITLINS_SH}"
      --bids-dir    "${BIDS_DIR}"
      --model       "${MODEL_JSON}"
      --subjects    "${SUBJECTS}"
      --deriv-root  "${DERIV_ROOT}"
      --deriv-subdir "${DERIV_SUBDIR}"
      --deriv-label "${DERIV_LABEL}"
      --out-parent  "${OUT_PARENT}"
      --out-suffix  "${OUT_SUFFIX}"
      --smooth      "${SMOOTH}"
      --space       "${SPACE}"
      --ncpus       "${NCPUS}"
      --mem-gb      "${MEM_GB}"
    )
    [[ "${DEBUG}" == "1" ]] && FITLINS_ARGS+=(--debug)

    run_cmd "${FITLINS_ARGS[@]}"
  fi
fi

# ----------------------------------------
# Step: fixreport
# ----------------------------------------
if has_step "fixreport"; then
  [[ -f "${FIX_REPORT_PY}" ]] || die "fix_fitlins_reports.py not found: ${FIX_REPORT_PY}"

  REPORT_FILE="$(ls -1 ${REPORT_GLOB_PATTERN} 2>/dev/null | head -n 1 || true)"
  if [[ -z "${REPORT_FILE}" ]]; then
    echo "[SKIP:fixreport] No report found under: ${OUT_DIR}/reports/"
  else
    if [[ "${FORCE}" != "1" && -f "${REPORT_FILE}.fixed" ]]; then
      echo "[SKIP:fixreport] Already fixed: ${REPORT_FILE}.fixed (use --force to rerun)"
    fi
    run_cmd python3 "${FIX_REPORT_PY}" "${REPORT_FILE}" --verbose
  fi
fi

# ----------------------------------------
# Step: plot-run
# ----------------------------------------
if has_step "plot-run"; then
  [[ -f "${PLOT_STATMAPS_PY}" ]] || die "plot_fmri_statmaps.py not found: ${PLOT_STATMAPS_PY}"

  if [[ -n "${PLOT_OUTDIR_RUN}" ]]; then
    OUTFIG_RUN="${PLOT_OUTDIR_RUN}"
  else
    _RUN_NODE_BARE="${NODES_RUN#node-}"
    OUTFIG_RUN="${FIG_PARENT}/${OUT_SUFFIX}/${_RUN_NODE_BARE}_${THR_MODE}"
    [[ "${THR_MODE}" == "p-unc" ]]                               && OUTFIG_RUN+="_p${P_UNC}"
    [[ "${THR_MODE}" == "fixed" ]]                               && OUTFIG_RUN+="_t${THR_FIXED}"
    [[ "${THR_MODE}" == "fdr" || "${THR_MODE}" == "bonferroni" || "${THR_MODE}" == "ari" ]] \
                                                                 && OUTFIG_RUN+="_a${ALPHA}"
    [[ "${TWO_SIDED}"  == "1" ]]    && OUTFIG_RUN+="_2s" || OUTFIG_RUN+="_1s"
    [[ -n "${DF_OVERRIDE}" ]]       && OUTFIG_RUN+="_df${DF_OVERRIDE}"
    [[ -n "${ROI_MASK}" ]]          && OUTFIG_RUN+="_svc"
  fi

  run_cmd python3 "${PLOT_STATMAPS_PY}" \
    --root   "${OUT_DIR}" \
    --nodes  ${NODES_RUN} \
    --glob   "${PLOT_GLOB}" \
    --outdir "${OUTFIG_RUN}" \
    --subjects    ${SUBJECTS} \
    --contrasts   ${CONTRASTS} \
    "${TASKS_FLAG[@]}" \
    "${RUNS_FLAG[@]}" \
    "${STATS_FLAG[@]}" \
    "${THR_ARGS[@]}" \
    "${TWO_SIDED_FLAG[@]}" \
    "${DF_FLAG[@]}" \
    --display-mode "${DISPLAY_MODE}" \
    "${CUT_COORDS_FLAG[@]}" \
    "${PLOT_ABS_FLAG[@]}" \
    "${VMAX_FLAG[@]}"\
    "${CLUSTER_EXTENT_FLAG[@]}"\
    "${ROI_MASK_FLAG[@]}"\
    "${CLUSTER_TABLE_FLAG[@]}"\
    "${VIEW3D_FLAG[@]}"
fi

# ----------------------------------------
# Step: plot-group
# Loops over every group-level node automatically.
# ----------------------------------------
if has_step "plot-group"; then
  [[ -f "${PLOT_STATMAPS_PY}" ]] || die "plot_fmri_statmaps.py not found: ${PLOT_STATMAPS_PY}"
  [[ -n "${GROUP_NODES}" ]] || die "No group-level nodes found. Check model JSON or pass --plot-nodes-group."

  # Count group nodes to decide whether to disambiguate outdir names
  _N_GROUP_NODES=$(echo "${GROUP_NODES}" | wc -w)

  for NODE_BARE in ${GROUP_NODES}; do
    NODE_ARG="node-${NODE_BARE}"

    if [[ -n "${PLOT_OUTDIR_GROUP}" ]]; then
      # User override: append node name when there are multiple group nodes
      if [[ "${_N_GROUP_NODES}" -gt 1 ]]; then
        OUTFIG_GROUP="${PLOT_OUTDIR_GROUP}__${NODE_BARE}"
      else
        OUTFIG_GROUP="${PLOT_OUTDIR_GROUP}"
      fi
    else
      OUTFIG_GROUP="${FIG_PARENT}/${OUT_SUFFIX}/${NODE_BARE}_${THR_MODE}"
      [[ "${THR_MODE}" == "p-unc" ]]                               && OUTFIG_GROUP+="_p${P_UNC}"
      [[ "${THR_MODE}" == "fixed" ]]                               && OUTFIG_GROUP+="_t${THR_FIXED}"
      [[ "${THR_MODE}" == "fdr" || "${THR_MODE}" == "bonferroni" || "${THR_MODE}" == "ari" ]] \
                                                                   && OUTFIG_GROUP+="_a${ALPHA}"
      [[ "${TWO_SIDED}" == "1" ]]    && OUTFIG_GROUP+="_2s" || OUTFIG_GROUP+="_1s"
      [[ -n "${DF_OVERRIDE}" ]]      && OUTFIG_GROUP+="_df${DF_OVERRIDE}"
      [[ -n "${ROI_MASK}" ]]         && OUTFIG_GROUP+="_svc"
    fi

    echo "[plot-group] Node: ${NODE_ARG}  ->  ${OUTFIG_GROUP}"

    run_cmd python3 "${PLOT_STATMAPS_PY}" \
      --root   "${OUT_DIR}" \
      --nodes  "${NODE_ARG}" \
      --glob   "${PLOT_GLOB}" \
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
      "${VMAX_FLAG[@]}"\
      "${CLUSTER_EXTENT_FLAG[@]}"\
      "${ROI_MASK_FLAG[@]}"\
      "${CLUSTER_TABLE_FLAG[@]}"\
      "${VIEW3D_FLAG[@]}"
  done
fi

# ----------------------------------------
# Step: report
# ----------------------------------------
if has_step "report"; then
  [[ -f "${REPORT_PY}" ]] || die "generate_model_report.py not found: ${REPORT_PY}"
  [[ -n "${TASK_GROUP}" ]] || die "TASK_GROUP is required for the report step"

  mkdir -p "$(dirname "${REPORT_OUTPUT}")"

  run_cmd python3 "${REPORT_PY}" \
    --task-group "${TASK_GROUP}" \
    --models "${MODEL}" \
    --work-root "${WORK_ROOT}" \
    --kernel "${KERNEL_MM}" \
    --thr-suffix "${REPORT_THR_SUFFIX}" \
    --output "${REPORT_OUTPUT}"
fi

# ----------------------------------------
echo
echo "== DONE =="
echo "Outputs: ${OUT_DIR}"
echo "Figures: ${FIG_PARENT}/${OUT_SUFFIX}/"
echo "Report:  ${REPORT_OUTPUT}"
echo "Group:   ${TASK_GROUP:-<none>}"
