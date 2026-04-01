#!/usr/bin/env bash
set -euo pipefail

# =========================
# FitLins runner (path-agnostic)
# =========================
# Goals:
# - Works with any BIDS root, any model JSON, any task (as defined by the model)
# - Output/slb_work dirs can live anywhere (we bind what we need)
# - Derivatives root/label configurable
# - Container configurable
#
# Assumptions:
# - apptainer is available on host
# - FitLins binary exists inside container (default: /opt/conda/envs/fitlins/bin/fitlins)
#
# Notes:
# - We keep --drop-missing (safe default for early-stage sanity-check GLMs)
# - We do NOT use --ignore hacks; you already fixed the root cause in the container.

# -------------------------
# Defaults
# -------------------------
CONTAINER_DEFAULT="/data/sld/homes/vguigon/slb_work/containers/fitlins_patched/fitlins-0.11.0_pybids-0.15.6_patched.sif"
FITLINS_BIN_DEFAULT="/opt/conda/envs/fitlins/bin/fitlins"

DERIV_ROOT_DEFAULT="/data/sld/homes/collab/slb/derivatives"
DERIV_LABEL_DEFAULT="fmriprep"          # FitLins --derivative-label
DERIV_SUBDIR_DEFAULT="fmriprep"         # Typically same as label; can differ

SPACE_DEFAULT="MNI152NLin2009cAsym"
NCPUS_DEFAULT="8"
MEM_GB_DEFAULT="16"

# Smoothing spec: FWHM[:LEVEL[:TYPE]] e.g. 6:run:iso
SMOOTH_DEFAULT=""

# Work/output defaults (if not provided)
OUT_PARENT_DEFAULT="/data/sld/homes/vguigon/slb_work/fitlins_derivatives"
WORK_DIR_DEFAULT="/data/sld/homes/vguigon/slb_work/work_fitlins"

# -------------------------
# Usage
# -------------------------
usage() {
  cat <<EOF
Usage:
  $(basename "$0") --bids-dir <BIDS_ROOT> --model <MODEL_JSON> --subjects "<ids>" [options]

Required:
  --bids-dir <dir>         Path to BIDS root on host.
  --model <json>           Path to BIDS StatsModel JSON on host (absolute or relative).
  --subjects "<ids>"       Space-separated participant labels without 'sub-' prefix.
                           Example: "000 001 002 003"

Optional:
  --container <sif>        Apptainer SIF. Default: ${CONTAINER_DEFAULT}
  --fitlins-bin <path>     FitLins executable inside container. Default: ${FITLINS_BIN_DEFAULT}

  --deriv-root <dir>       Derivatives parent on host. Default: ${DERIV_ROOT_DEFAULT}
  --deriv-subdir <name>    Subdir under deriv-root to use. Default: ${DERIV_SUBDIR_DEFAULT}
                           (commonly "fmriprep")
  --deriv-label <label>    FitLins --derivative-label value. Default: ${DERIV_LABEL_DEFAULT}

  --out-parent <dir>       Parent folder for outputs on host. Default: ${OUT_PARENT_DEFAULT}
  --out-suffix <str>       Output folder name under out-parent. Default: model stem
  --slb_work-dir <dir>         Work dir on host (will create subfolder). Default: ${WORK_DIR_DEFAULT}
  --slb_work-suffix <str>      Work subfolder name. Default: model stem

  --space <space>          Space label. Default: ${SPACE_DEFAULT}
  --ncpus <int>            Default: ${NCPUS_DEFAULT}
  --mem-gb <int>           Default: ${MEM_GB_DEFAULT}
  --smooth <spec>          Smoothing spec: FWHM[:LEVEL[:TYPE]]. Default: (none)

  --debug                  Pass --debug to fitlins
  --dry-run                Print command, do not execute
  -h, --help               Show this help

Examples:
  $(basename "$0") --bids-dir /path/to/bids --model /path/to/model.json --subjects "000 001"
  $(basename "$0") --bids-dir /path/to/bids --model model.json --subjects "000" --smooth "6:run:iso"
EOF
}

# -------------------------
# Helpers
# -------------------------
realpath_fallback() {
  # portable-ish realpath
  if command -v realpath >/dev/null 2>&1; then
    realpath "$1"
  else
    python -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$1"
  fi
}

add_bind() {
  # Add bind if host path exists and not already present.
  # Accepts a directory; binds dir->dir (same inside container) for simplicity.
  local host_dir="$1"
  host_dir="$(realpath_fallback "$host_dir")"
  [[ -d "$host_dir" ]] || { echo "ERROR: bind target is not a directory: $host_dir" >&2; exit 1; }

  for b in "${BINDS[@]:-}"; do
    if [[ "$b" == "${host_dir}:${host_dir}" ]]; then
      return 0
    fi
  done
  BINDS+=( "${host_dir}:${host_dir}" )
}

ensure_parent_dir() {
  local p="$1"
  mkdir -p "$p"
}

# -------------------------
# Args
# -------------------------
BIDS_DIR=""
MODEL_JSON=""
SUBJECTS_STR=""

CONTAINER="${CONTAINER_DEFAULT}"
FITLINS_BIN="${FITLINS_BIN_DEFAULT}"

DERIV_ROOT="${DERIV_ROOT_DEFAULT}"
DERIV_SUBDIR="${DERIV_SUBDIR_DEFAULT}"
DERIV_LABEL="${DERIV_LABEL_DEFAULT}"

OUT_PARENT="${OUT_PARENT_DEFAULT}"
OUT_SUFFIX=""
WORK_DIR="${WORK_DIR_DEFAULT}"
WORK_SUFFIX=""

SPACE="${SPACE_DEFAULT}"
NCPUS="${NCPUS_DEFAULT}"
MEM_GB="${MEM_GB_DEFAULT}"
SMOOTH="${SMOOTH_DEFAULT}"

DEBUG="0"
DRY_RUN="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bids-dir)      BIDS_DIR="${2:-}"; shift 2;;
    --model)         MODEL_JSON="${2:-}"; shift 2;;
    --subjects)      SUBJECTS_STR="${2:-}"; shift 2;;

    --container)     CONTAINER="${2:-}"; shift 2;;
    --fitlins-bin)   FITLINS_BIN="${2:-}"; shift 2;;

    --deriv-root)    DERIV_ROOT="${2:-}"; shift 2;;
    --deriv-subdir)  DERIV_SUBDIR="${2:-}"; shift 2;;
    --deriv-label)   DERIV_LABEL="${2:-}"; shift 2;;

    --out-parent)    OUT_PARENT="${2:-}"; shift 2;;
    --out-suffix)    OUT_SUFFIX="${2:-}"; shift 2;;
    --slb_work-dir)      WORK_DIR="${2:-}"; shift 2;;
    --slb_work-suffix)   WORK_SUFFIX="${2:-}"; shift 2;;

    --space)         SPACE="${2:-}"; shift 2;;
    --ncpus)         NCPUS="${2:-}"; shift 2;;
    --mem-gb)        MEM_GB="${2:-}"; shift 2;;
    --smooth)        SMOOTH="${2:-}"; shift 2;;

    --debug)         DEBUG="1"; shift 1;;
    --dry-run)       DRY_RUN="1"; shift 1;;
    -h|--help)       usage; exit 0;;
    *) echo "ERROR: unknown argument: $1" >&2; usage; exit 2;;
  esac
done

# -------------------------
# Validate required args
# -------------------------
[[ -n "${BIDS_DIR}" ]]     || { echo "ERROR: --bids-dir is required" >&2; usage; exit 2; }
[[ -n "${MODEL_JSON}" ]]   || { echo "ERROR: --model is required" >&2; usage; exit 2; }
[[ -n "${SUBJECTS_STR}" ]] || { echo "ERROR: --subjects is required" >&2; usage; exit 2; }

BIDS_DIR="$(realpath_fallback "${BIDS_DIR}")"
MODEL_JSON="$(realpath_fallback "${MODEL_JSON}")"
DERIV_ROOT="$(realpath_fallback "${DERIV_ROOT}")"
OUT_PARENT="$(realpath_fallback "${OUT_PARENT}")"
WORK_DIR="$(realpath_fallback "${WORK_DIR}")"
CONTAINER="$(realpath_fallback "${CONTAINER}")"

[[ -d "${BIDS_DIR}" ]]      || { echo "ERROR: BIDS dir not found: ${BIDS_DIR}" >&2; exit 1; }
[[ -f "${MODEL_JSON}" ]]    || { echo "ERROR: model JSON not found: ${MODEL_JSON}" >&2; exit 1; }
[[ -d "${DERIV_ROOT}" ]]    || { echo "ERROR: derivatives root not found: ${DERIV_ROOT}" >&2; exit 1; }
[[ -f "${CONTAINER}" ]]     || { echo "ERROR: container not found: ${CONTAINER}" >&2; exit 1; }

DERIV_PATH="${DERIV_ROOT%/}/${DERIV_SUBDIR}"
[[ -d "${DERIV_PATH}" ]]    || { echo "ERROR: derivatives subdir not found: ${DERIV_PATH}" >&2; exit 1; }

# -------------------------
# Derive default suffixes from model name if not provided
# -------------------------
MODEL_BASENAME="$(basename "${MODEL_JSON}")"
MODEL_STEM="${MODEL_BASENAME%.json}"
MODEL_STEM="${MODEL_STEM%_smdl}"

[[ -n "${OUT_SUFFIX}" ]]  || OUT_SUFFIX="${MODEL_STEM}"
[[ -n "${WORK_SUFFIX}" ]] || WORK_SUFFIX="${MODEL_STEM}"

OUT_DIR_HOST="${OUT_PARENT%/}/${OUT_SUFFIX}"
WORK_DIR_HOST="${WORK_DIR%/}/work_fitlins_tmp_${WORK_SUFFIX}"

ensure_parent_dir "${OUT_DIR_HOST}"
ensure_parent_dir "${WORK_DIR_HOST}"

# -------------------------
# Subjects array
# -------------------------
# shellcheck disable=SC2206
SUBJ_ARR=(${SUBJECTS_STR})

# -------------------------
# Bind mounts (path-agnostic strategy)
# -------------------------
# We bind directories to themselves (host path == container path) so we never need translation logic.
BINDS=()

# Bind BIDS root dir itself
add_bind "${BIDS_DIR}"

# Bind derivatives root (so the /.../derivatives path exists in container)
add_bind "${DERIV_ROOT}"

# Bind model JSON parent directory (so the exact file path exists in container)
add_bind "$(dirname "${MODEL_JSON}")"

# Bind output/slb_work parents
add_bind "$(dirname "${OUT_DIR_HOST}")"
add_bind "$(dirname "${WORK_DIR_HOST}")"

# If your BIDS root is a symlink into another tree and PyBIDS resolves real paths,
# binding the real path as well can prevent "file not found" surprises.
# This is cheap and safe.
add_bind "$(realpath_fallback "${BIDS_DIR}")"
add_bind "$(realpath_fallback "${DERIV_ROOT}")"

# -------------------------
# Print config
# -------------------------
echo "== FitLins runner (agnostic) =="
echo "Container:      ${CONTAINER}"
echo "FitLins bin:    ${FITLINS_BIN}"
echo "BIDS dir:       ${BIDS_DIR}"
echo "Model JSON:     ${MODEL_JSON}"
echo "Derivatives:    ${DERIV_PATH}"
echo "Deriv label:    ${DERIV_LABEL}"
echo "Space:          ${SPACE}"
echo "NCPUs:          ${NCPUS}"
echo "Mem (GB):       ${MEM_GB}"
echo "Subjects:       ${SUBJ_ARR[*]}"
echo "Out dir:        ${OUT_DIR_HOST}"
echo "Work dir:       ${WORK_DIR_HOST}"
if [[ -n "${SMOOTH}" ]]; then
  echo "Smoothing:      ${SMOOTH}"
else
  echo "Smoothing:      (none)"
fi
echo "Binds:"
for b in "${BINDS[@]}"; do
  echo "  -B ${b}"
done
echo

# -------------------------
# Command
# -------------------------
CMD=(
  apptainer exec --cleanenv --no-home
  --env MPLCONFIGDIR=/tmp/mplconfig
  "${BINDS[@]/#/-B }"
  "${CONTAINER}"
  "${FITLINS_BIN}"
  "${BIDS_DIR}"
  "${OUT_DIR_HOST}"
  participant
  --derivatives "${DERIV_PATH}"
  --derivative-label "${DERIV_LABEL}"
  --model "${MODEL_JSON}"
  --participant-label "${SUBJ_ARR[@]}"
  --space "${SPACE}"
  --slb_work-dir "${WORK_DIR_HOST}"
  --n-cpus "${NCPUS}"
  --mem-gb "${MEM_GB}"
  --drop-missing
)

if [[ -n "${SMOOTH}" ]]; then
  CMD+=( -s "${SMOOTH}" )
fi

if [[ "${DEBUG}" == "1" ]]; then
  CMD+=( --debug )
fi

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
echo "Outputs: ${OUT_DIR_HOST}"
