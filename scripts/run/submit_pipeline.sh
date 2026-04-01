#!/usr/bin/env bash
# =============================================================================
# submit_pipeline.sh
#
# SLURM wrapper for run_pipeline.sh.
# Generates a self-contained sbatch script and submits it so the pipeline
# runs on a compute node, not the master/login node.
#
# Usage:
#   submit_pipeline.sh [--config <cfg>] [--slurm-*] [run_pipeline.sh flags...]
#
# The script accepts all run_pipeline.sh flags transparently, plus the
# SLURM-specific flags documented below.  Config files are also supported and
# follow the same convention as run_pipeline.sh (source a bash file).
#
# SLURM flags can also be set in the config file:
#   SLURM_PARTITION="compute"  # only one partition on sld
#   SLURM_TIME=""               # leave empty for infinite partitions
#   SLURM_MAIL_USER="you@institution.edu"
#   SLURM_MAIL_TYPE="END,FAIL"
#   SLURM_ACCOUNT=""           # leave empty if your cluster does not use accounts
#   SLURM_CONSTRAINT=""        # node feature constraint (e.g. "avx2")
#   SLURM_QOS=""               # QoS name if required
#
# Design notes:
#   - run_pipeline.sh owns the actual analysis/report steps; this wrapper simply
#     calls it inside sbatch.
#   - Resources (--cpus-per-task, --mem) are derived from NCPUS / MEM_GB, which
#     are already defined in your per-model config files.
#   - A timestamped sbatch script is written to LOGS_DIR for full reproducibility:
#     you can inspect exactly what was submitted even after the job finishes.
#   - --dry-run prints the sbatch script without submitting.
#   - --no-submit writes the sbatch script and prints the path but does not call sbatch.
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Locate run_pipeline.sh (must be in the same directory as this script,
# or set PIPELINE_SH in your config / environment).
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPELINE_SH="${PIPELINE_SH:-${SCRIPT_DIR}/run_pipeline.sh}"

# ---------------------------------------------------------------------------
# Defaults — pipeline params (mirrors run_pipeline.sh defaults so that
# resource allocation is correct even without an explicit config)
# ---------------------------------------------------------------------------
NCPUS="8"
MEM_GB="16"
MODEL=""
TASK_GROUP=""   # mirrors run_pipeline.sh; used to namespace LOGS_DIR
STEPS="all"
DRY_RUN="0"

# ---------------------------------------------------------------------------
# SLURM defaults (override in config file or via --slurm-* flags)
# ---------------------------------------------------------------------------
SLURM_PARTITION="compute"
SLURM_TIME=""               # Leave empty for clusters with infinite time limit (like sld)
SLURM_MAIL_USER=""          # e.g. you@institution.edu
SLURM_MAIL_TYPE="END,FAIL"
SLURM_ACCOUNT=""            # project account if required (leave empty if not)
SLURM_CONSTRAINT=""         # e.g. "avx2" (leave empty if not needed)
SLURM_QOS=""                # leave empty if not needed
NO_SUBMIT="0"               # set to 1 with --no-submit to write script but not submit

# Log directory for sbatch scripts and SLURM stdout/stderr.
# Override with --logs-dir or LOGS_DIR= in your config file.
LOGS_DIR="/data/sld/homes/vguigon/slb_work/slurm_logs"

# ---------------------------------------------------------------------------
# Config file loading (must mirror run_pipeline.sh so NCPUS / MEM_GB are set
# before we build the sbatch header)
# ---------------------------------------------------------------------------
_PASSTHROUGH_ARGS=()   # all args that go straight to run_pipeline.sh

if [[ "${1:-}" == "--config" ]]; then
    _CONFIG_FILE="${2:-}"
    [[ -f "${_CONFIG_FILE}" ]] || { echo "ERROR: config file not found: ${_CONFIG_FILE}" >&2; exit 1; }
    # shellcheck source=/dev/null
    source "${_CONFIG_FILE}"
    echo "[submit] Loaded config: ${_CONFIG_FILE}"
    _PASSTHROUGH_ARGS+=(--config "${_CONFIG_FILE}")
    shift 2
fi

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<EOF
Usage:
  $(basename "$0") [--config <cfg>] [--slurm-*] [run_pipeline.sh flags...]

SLURM-specific flags (all optional; also settable in config file):
  --slurm-partition <p>   SLURM partition. Default: ${SLURM_PARTITION}
  --slurm-time <HH:MM:SS> Wall-clock time limit. Default: (none — omitted when empty, suitable for infinite partitions)
  --slurm-account <a>     Account/project (omit if not required). Default: (none)
  --slurm-mail <email>    Email for job notifications. Default: (none)
  --slurm-mail-type <t>   Mail event types. Default: ${SLURM_MAIL_TYPE}
  --slurm-constraint <c>  Node feature constraint. Default: (none)
  --slurm-qos <q>         QoS name. Default: (none)
  --logs-dir <dir>        Where sbatch scripts + SLURM logs go. Default: ${LOGS_DIR}

  --no-submit             Write sbatch script but do not call sbatch.
  --dry-run               Print sbatch script to stdout; do not write or submit.

All other flags are passed transparently to run_pipeline.sh.
See run_pipeline.sh --help for the full list.

Examples:
  # Submit with a config file (resources derived from NCPUS/MEM_GB in config):
  $(basename "$0") --config configs/tmth_visual_vs_fixation_compcor.cfg

  # Override time limit and partition:
  $(basename "$0") --config configs/tmth.cfg --slurm-time 12:00:00 --slurm-partition long

  # Submit plotting plus PDF report generation:
  $(basename "$0") --config configs/tmth.cfg --steps plot-run,plot-group,report

  # Write the sbatch script for inspection without running:
  $(basename "$0") --config configs/tmth.cfg --no-submit

  # Dry run (print sbatch script to stdout):
  $(basename "$0") --config configs/tmth.cfg --dry-run
EOF
}

# ---------------------------------------------------------------------------
# Parse args — split SLURM flags from run_pipeline.sh passthrough args
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --slurm-partition)  SLURM_PARTITION="${2:-}"; shift 2;;
        --slurm-time)       SLURM_TIME="${2:-}"; shift 2;;
        --slurm-account)    SLURM_ACCOUNT="${2:-}"; shift 2;;
        --slurm-mail)       SLURM_MAIL_USER="${2:-}"; shift 2;;
        --slurm-mail-type)  SLURM_MAIL_TYPE="${2:-}"; shift 2;;
        --slurm-constraint) SLURM_CONSTRAINT="${2:-}"; shift 2;;
        --slurm-qos)        SLURM_QOS="${2:-}"; shift 2;;
        --logs-dir)         LOGS_DIR="${2:-}"; shift 2;;
        --no-submit)        NO_SUBMIT="1"; shift 1;;
        --dry-run)          DRY_RUN="1"; shift 1;;
        -h|--help)          usage; exit 0;;
        # Intercept --model-stem and --ncpus/--mem-gb so we can use them in the
        # sbatch header, but also pass them through to run_pipeline.sh.
        --model-stem)
            MODEL="${2:-}"
            _PASSTHROUGH_ARGS+=("$1" "$2"); shift 2;;
        --task-group)
            TASK_GROUP="${2:-}"
            _PASSTHROUGH_ARGS+=("$1" "$2"); shift 2;;
        --ncpus)
            NCPUS="${2:-}"
            _PASSTHROUGH_ARGS+=("$1" "$2"); shift 2;;
        --mem-gb)
            MEM_GB="${2:-}"
            _PASSTHROUGH_ARGS+=("$1" "$2"); shift 2;;
        *)
            _PASSTHROUGH_ARGS+=("$1"); shift 1;;
    esac
done

# ---------------------------------------------------------------------------
# Apply TASK_GROUP namespacing to LOGS_DIR
# ---------------------------------------------------------------------------
if [[ -n "${TASK_GROUP}" ]]; then
    LOGS_DIR="${LOGS_DIR%/}/${TASK_GROUP}"
fi

# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------
[[ -f "${PIPELINE_SH}" ]] || {
    echo "ERROR: run_pipeline.sh not found: ${PIPELINE_SH}" >&2
    echo "  Set PIPELINE_SH=/path/to/run_pipeline.sh or place this script alongside it." >&2
    exit 1
}
[[ -n "${MODEL}" ]] || {
    echo "ERROR: model not set. Pass --model-stem or set MODEL= in your config." >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Derive job name and log paths
# ---------------------------------------------------------------------------
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
JOB_NAME="fitlins_${MODEL}"
mkdir -p "${LOGS_DIR}"

SBATCH_SCRIPT="${LOGS_DIR}/${JOB_NAME}_${TIMESTAMP}.sbatch"
SLURM_OUT="${LOGS_DIR}/${JOB_NAME}_${TIMESTAMP}_%j.out"
SLURM_ERR="${LOGS_DIR}/${JOB_NAME}_${TIMESTAMP}_%j.err"

# ---------------------------------------------------------------------------
# Build the sbatch script
# ---------------------------------------------------------------------------

# Optional directives (only emit if value is non-empty)
_optional_directives=""
[[ -n "${SLURM_ACCOUNT}" ]]    && _optional_directives+="#SBATCH --account=${SLURM_ACCOUNT}"$'\n'
[[ -n "${SLURM_CONSTRAINT}" ]] && _optional_directives+="#SBATCH --constraint=${SLURM_CONSTRAINT}"$'\n'
[[ -n "${SLURM_QOS}" ]]        && _optional_directives+="#SBATCH --qos=${SLURM_QOS}"$'\n'
[[ -n "${SLURM_MAIL_USER}" ]]  && _optional_directives+="#SBATCH --mail-user=${SLURM_MAIL_USER}"$'\n'
[[ -n "${SLURM_MAIL_USER}" ]]  && _optional_directives+="#SBATCH --mail-type=${SLURM_MAIL_TYPE}"$'\n'

# Build the quoted passthrough argument string for the sbatch body
_ARGS_STR=""
for arg in "${_PASSTHROUGH_ARGS[@]}"; do
    # Shell-quote each argument so spaces/special chars survive the heredoc
    _ARGS_STR+=" $(printf '%q' "$arg")"
done

# Build optional time directive
_time_directive=""
[[ -n "${SLURM_TIME}" ]] && _time_directive="#SBATCH --time=${SLURM_TIME}"$'\n'

SBATCH_CONTENT="#!/usr/bin/env bash
# Auto-generated by submit_pipeline.sh on ${TIMESTAMP}
# Model: ${MODEL}
# Submitted from: $(hostname)
# Working directory at submission: $(pwd)
# =============================================================================

#SBATCH --job-name=${JOB_NAME}
#SBATCH --partition=${SLURM_PARTITION}
${_time_directive}#SBATCH --cpus-per-task=${NCPUS}
#SBATCH --mem=${MEM_GB}G
#SBATCH --output=${SLURM_OUT}
#SBATCH --error=${SLURM_ERR}
${_optional_directives}
# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
set -euo pipefail

echo \"== SLURM job started ==\"
echo \"  Job ID:      \${SLURM_JOB_ID}\"
echo \"  Job name:    \${SLURM_JOB_NAME}\"
echo \"  Node:        \$(hostname)\"
echo \"  CPUs:        \${SLURM_CPUS_PER_TASK}\"
echo \"  Mem (GB):    ${MEM_GB}\"
echo \"  Partition:   \${SLURM_JOB_PARTITION}\"
echo \"  Started:     \$(date)\"
echo

# ---------------------------------------------------------------------------
# Run the pipeline
# ---------------------------------------------------------------------------
${PIPELINE_SH}${_ARGS_STR}

echo
echo \"== SLURM job finished ==\"
echo \"  Ended: \$(date)\"
"

# ---------------------------------------------------------------------------
# Dry run: print to stdout and exit
# ---------------------------------------------------------------------------
if [[ "${DRY_RUN}" == "1" ]]; then
    echo "== [DRY RUN] sbatch script (not written, not submitted) =="
    echo "----------------------------------------"
    echo "${SBATCH_CONTENT}"
    echo "----------------------------------------"
    exit 0
fi

# ---------------------------------------------------------------------------
# Write sbatch script
# ---------------------------------------------------------------------------
echo "${SBATCH_CONTENT}" > "${SBATCH_SCRIPT}"
chmod 644 "${SBATCH_SCRIPT}"
echo "[submit] sbatch script written: ${SBATCH_SCRIPT}"

# ---------------------------------------------------------------------------
# No-submit mode: stop here
# ---------------------------------------------------------------------------
if [[ "${NO_SUBMIT}" == "1" ]]; then
    echo "[submit] --no-submit set. Script NOT submitted."
    echo "  To submit manually: sbatch ${SBATCH_SCRIPT}"
    exit 0
fi

# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------
echo "[submit] Submitting to SLURM..."
JOB_ID="$(sbatch --parsable "${SBATCH_SCRIPT}")"
echo
echo "== Submitted successfully =="
echo "  Job ID:      ${JOB_ID}"
echo "  Job name:    ${JOB_NAME}"
echo "  Partition:   ${SLURM_PARTITION}"
echo "  CPUs:        ${NCPUS}"
echo "  Mem (GB):    ${MEM_GB}"
echo "  Time limit:  ${SLURM_TIME:-unlimited}"
echo "  sbatch file: ${SBATCH_SCRIPT}"
echo "  stdout log:  ${SLURM_OUT/\%j/${JOB_ID}}"
echo "  stderr log:  ${SLURM_ERR/\%j/${JOB_ID}}"
echo
echo "  Monitor:     squeue -j ${JOB_ID}"
echo "  Cancel:      scancel ${JOB_ID}"
