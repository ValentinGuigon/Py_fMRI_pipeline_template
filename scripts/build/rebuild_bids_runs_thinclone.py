#!/usr/bin/env python3
"""
rebuild_bids_runs_thinclone.py
==============================
Creates a thin-clone working BIDS directory from a source tree by:
  - Symlinking heavy imaging files (NIfTI) to the source
  - Materializing all metadata (JSON, TSV, etc.) as real copies

This avoids mixed provenance between read-only shared data and locally
edited metadata, while keeping disk usage minimal.

Usage:
  python3 rebuild_bids_runs_thinclone.py --src <SOURCE> --dst <DEST>

Arguments:
  --src    Source BIDS directory (read-only shared tree).
  --dst    Destination directory to create. Must not already exist.

The script refuses to overwrite an existing destination. Remove it first
if you need to rebuild from scratch.
"""
import argparse
import os
import shutil
from pathlib import Path


# Files to SYMLINK (heavy imaging files)
SYMLINK_EXTS = {".nii", ".nii.gz"}


def is_nii_gz(p: Path) -> bool:
    return p.name.endswith(".nii.gz")


def suffix_key(p: Path) -> str:
    if is_nii_gz(p):
        return ".nii.gz"
    return p.suffix


def ensure_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def safe_unlink(p: Path) -> None:
    if p.is_symlink() or p.is_file():
        p.unlink()


def build_clone(src: Path, dst: Path) -> None:
    n_link = 0
    n_copy = 0

    for src_path in src.rglob("*"):
        rel = src_path.relative_to(src)
        dst_path = dst / rel

        if src_path.is_dir():
            dst_path.mkdir(parents=True, exist_ok=True)
            continue

        ext = suffix_key(src_path)
        ensure_dir(dst_path)

        # Resolve symlinks in source so our links/copies point to real files
        real_src = src_path.resolve() if src_path.is_symlink() else src_path

        if ext in SYMLINK_EXTS:
            safe_unlink(dst_path)
            os.symlink(str(real_src), str(dst_path))
            n_link += 1
        else:
            safe_unlink(dst_path)
            shutil.copy2(real_src, dst_path)
            n_copy += 1

    print(f"Done.  Linked: {n_link}  Copied: {n_copy}")


def integrity_check(dst: Path) -> None:
    broken = []
    for p in dst.rglob("*"):
        if p.is_symlink():
            try:
                p.resolve(strict=True)
            except FileNotFoundError:
                broken.append(p)

    if broken:
        print(f"ERROR: {len(broken)} broken symlink(s) in destination:")
        for b in broken[:50]:
            print(f"  {b}  ->  {os.readlink(b)}")
        raise SystemExit(
            "Thin clone created but has broken symlinks. "
            "Check bind paths and source tree integrity."
        )
    else:
        print("Integrity check passed: no broken symlinks.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--src",
        type=Path,
        required=True,
        help="Source BIDS directory (read-only shared tree).",
    )
    ap.add_argument(
        "--dst",
        type=Path,
        required=True,
        help="Destination directory to create. Must not already exist.",
    )
    args = ap.parse_args()

    src: Path = args.src
    dst: Path = args.dst

    if not src.exists():
        raise SystemExit(f"ERROR: source directory does not exist: {src}")

    if dst.exists():
        raise SystemExit(
            f"ERROR: destination already exists: {dst}\n"
            "Refusing to overwrite. Remove it or choose a different path."
        )

    print(f"SRC: {src}")
    print(f"DST: {dst}")
    print("Building thin clone: symlinking imaging files, copying metadata...")

    dst.mkdir(parents=True)
    build_clone(src, dst)
    integrity_check(dst)


if __name__ == "__main__":
    main()