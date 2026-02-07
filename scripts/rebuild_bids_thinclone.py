#!/usr/bin/env python3
import os
import shutil
from pathlib import Path

# --------------------
# Configure here
# --------------------
SRC = Path("/data/sld/homes/collab/slb/bids")
DST = Path("/data/sld/homes/vguigon/work/slb_bids_v2")

# Files to SYMLINK (heavy)
SYMLINK_EXTS = {
    ".nii", ".nii.gz",  # imaging
}

# Files to COPY as real files (metadata, tables, gradients, events, etc.)
# Default behavior: copy everything not in SYMLINK_EXTS
# You can add exceptions if you want.
# --------------------

def is_nii_gz(p: Path) -> bool:
    return p.name.endswith(".nii.gz")

def suffix_key(p: Path) -> str:
    # treat .nii.gz as one extension key
    if is_nii_gz(p):
        return ".nii.gz"
    return p.suffix

def ensure_dir(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)

def safe_unlink(p: Path):
    if p.is_symlink() or p.is_file():
        p.unlink()

def main():
    if not SRC.exists():
        raise SystemExit(f"SRC does not exist: {SRC}")

    if DST.exists():
        raise SystemExit(f"DST already exists: {DST}\nRefusing to overwrite. Remove it or choose a new path.")

    print(f"SRC: {SRC}")
    print(f"DST: {DST}")
    print("Building thin clone: symlink imaging, copy metadata...")

    # Walk source tree
    n_link = 0
    n_copy = 0

    for src_path in SRC.rglob("*"):
        rel = src_path.relative_to(SRC)
        dst_path = DST / rel

        if src_path.is_dir():
            dst_path.mkdir(parents=True, exist_ok=True)
            continue

        # Copy symlinks by resolving to real file content if metadata,
        # but keep imaging files as symlinks to their real targets.
        ext = suffix_key(src_path)

        ensure_dir(dst_path)

        # If source is a symlink, resolve it to its target
        # for decision-making and copy/link actions.
        real_src = src_path
        if src_path.is_symlink():
            real_src = src_path.resolve()

        if ext in SYMLINK_EXTS:
            # Create absolute symlink to resolved target (stable for containers if target tree is bind-mounted)
            safe_unlink(dst_path)
            os.symlink(str(real_src), str(dst_path))
            n_link += 1
        else:
            # Materialize as a real file (copy bytes)
            safe_unlink(dst_path)
            shutil.copy2(real_src, dst_path)
            n_copy += 1

    print(f"Done. Linked: {n_link}  Copied: {n_copy}")

    # Quick integrity checks
    broken = []
    for p in DST.rglob("*"):
        if p.is_symlink():
            try:
                _ = p.resolve(strict=True)
            except FileNotFoundError:
                broken.append(p)

    if broken:
        print(f"ERROR: broken symlinks in DST: {len(broken)}")
        for b in broken[:50]:
            print("  ", b, "->", os.readlink(b))
        raise SystemExit("Thin clone created but has broken symlinks. Check bind paths and SRC integrity.")
    else:
        print("Integrity check: no broken symlinks.")

if __name__ == "__main__":
    main()
