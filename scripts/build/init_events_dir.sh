#!/usr/bin/env bash
# init_events_dir.sh
# ==================
# Initializes the slb_events/ directory structure:
#
#   slb_events/
#     original/    <- verbatim copies of events TSVs from slb_bids_runs (reference)
#     motor/       <- augmented with button_press events (created by add_button_press_events.py)
#
# Run this once. After that:
#   - original/ is the reference, never modified
#   - motor/ is generated from original/ by add_button_press_events.py
#
# Usage:
#   bash init_events_dir.sh \
#     --bids-dir  /data/sld/homes/vguigon/slb_work/slb_bids_runs \
#     --events-dir /data/sld/homes/vguigon/slb_work/slb_events

set -euo pipefail

BIDS_DIR=""
EVENTS_DIR=""
AUGMENTED_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bids-dir)    BIDS_DIR="${2:-}";        shift 2;;
    --events-dir)  EVENTS_DIR="${2:-}";      shift 2;;
    --name)        AUGMENTED_NAME="${2:-}";  shift 2;;
    *) echo "Unknown arg: $1" >&2; exit 1;;
  esac
done

[[ -n "${BIDS_DIR}" ]]        || { echo "ERROR: --bids-dir required" >&2; exit 1; }
[[ -n "${EVENTS_DIR}" ]]      || { echo "ERROR: --events-dir required" >&2; exit 1; }
[[ -n "${AUGMENTED_NAME}" ]]  || { echo "ERROR: --name required (e.g. --name motor)" >&2; exit 1; }
[[ -d "${BIDS_DIR}" ]]        || { echo "ERROR: BIDS dir not found: ${BIDS_DIR}" >&2; exit 1; }

ORIGINAL_DIR="${EVENTS_DIR}/original"
MOTOR_DIR="${EVENTS_DIR}/${AUGMENTED_NAME}"

if [[ -d "${ORIGINAL_DIR}" ]]; then
  echo "ERROR: ${ORIGINAL_DIR} already exists. Remove it first if you want to reinitialize." >&2
  exit 1
fi

echo "Initializing events directory structure..."
echo "  BIDS_DIR   : ${BIDS_DIR}"
echo "  EVENTS_DIR : ${EVENTS_DIR}"
echo

# Copy all events TSVs preserving sub-XXX/func/ structure
n=0
while IFS= read -r -d '' src; do
  rel="${src#${BIDS_DIR}/}"
  dst="${ORIGINAL_DIR}/${rel}"
  mkdir -p "$(dirname "${dst}")"
  cp "${src}" "${dst}"
  (( n++ )) || true
done < <(find "${BIDS_DIR}" -name "*_events.tsv" -print0)

echo "[OK] Copied ${n} events TSV(s) to: ${ORIGINAL_DIR}"
echo

# Create empty motor/ directory as a placeholder
mkdir -p "${MOTOR_DIR}"
echo "[OK] Created placeholder: ${MOTOR_DIR}"
echo

echo "== Next steps =="
echo "1. Generate augmented events (example for motor):"
echo "   python3 add_button_press_events.py \\"
echo "     --src-bids-dir  ${ORIGINAL_DIR} \\"
echo "     --dst-events-dir ${MOTOR_DIR} \\"
echo "     --tasks tm th"
echo
echo "2. Set EVENTS_DIR in your motor cfg:"
echo "   EVENTS_DIR=\"${MOTOR_DIR}\""
echo
echo "3. Leave EVENTS_DIR unset in the visual cfg (no override needed)."