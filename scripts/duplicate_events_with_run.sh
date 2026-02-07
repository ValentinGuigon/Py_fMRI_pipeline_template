#!/usr/bin/env bash
set -euo pipefail

BIDS_ROOT="/data/sld/homes/vguigon/work/slb_bids"

echo "Creating run-duplicated events.tsv files (non-destructive)"
echo "BIDS root: ${BIDS_ROOT}"
echo

find "${BIDS_ROOT}" -type f -name "*_events.tsv" | while read -r src; do
  fname="$(basename "$src")"
  dir="$(dirname "$src")"

  # th1 -> th run-1
  if [[ "$fname" =~ task-th1_events.tsv$ ]]; then
    dst="${dir}/${fname/task-th1/task-th_run-1}"
  elif [[ "$fname" =~ task-th2_events.tsv$ ]]; then
    dst="${dir}/${fname/task-th2/task-th_run-2}"

  # tm1 -> tm run-1
  elif [[ "$fname" =~ task-tm1_events.tsv$ ]]; then
    dst="${dir}/${fname/task-tm1/task-tm_run-1}"
  elif [[ "$fname" =~ task-tm2_events.tsv$ ]]; then
    dst="${dir}/${fname/task-tm2/task-tm_run-2}"
  else
    continue
  fi

  if [[ -f "$dst" ]]; then
    echo "[SKIP] exists: $(basename "$dst")"
  else
    cp "$src" "$dst"
    echo "[OK]   $(basename "$src") → $(basename "$dst")"
  fi
done

echo
echo "Done."
