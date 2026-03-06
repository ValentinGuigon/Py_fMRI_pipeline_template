#!/usr/bin/env python3
# plot_fmri_statmaps.py
#
# Statmap visualization utility for FitLins (or any BIDS-ish) derivatives.
#
# Thresholding modes (--thr-mode):
#   none           No thresholding; show raw statmap.
#   fixed          Fixed threshold in the statmap's native units (--thr-fixed).
#   p-unc          Uncorrected p-value → threshold (--p-unc). Works for t and z maps.
#   fdr            FDR (Benjamini-Hochberg) voxel-level correction (--alpha).
#                  Requires z-maps; warns on t-maps.
#   bonferroni     Bonferroni FWER voxel-level correction (--alpha).
#                  Requires z-maps; warns on t-maps.
#   ari            All-Resolution Inference (Rosenblatt et al. 2018) via
#                  nilearn.glm.cluster_level_inference.  Produces a
#                  proportion-of-true-discoveries map rather than a binary
#                  threshold; set cluster-forming thresholds with --ari-thresholds.
#
# Cluster-level options:
#   --cluster-extent K   Discard clusters < K voxels after voxel-level thresholding.
#                        Combines with any thr-mode except none/ari.
#
# Small Volume Correction (SVC):
#   --roi-mask <path>    NIfTI mask to restrict FDR/bonferroni correction to an ROI.
#                        The mask is resampled to statmap space automatically.
#                        For p-unc and fixed modes the mask is applied as a spatial
#                        restriction only (voxels outside the mask are zeroed).
#
# Optional outputs:
#   --cluster-table      Write a TSV cluster summary (peak coordinate, cluster size,
#                        peak stat) alongside each figure, via nilearn.reporting.
#
# Note on stat type:
#   FDR and Bonferroni operate in z-scale (nilearn converts alpha → z threshold).
#   When applied to t-statmaps the threshold will be misscaled; use only with z-maps
#   (group-level FitLins outputs) or convert t→z beforehand.
#
# Note on folder output:
#   _1s suffix refers to one-sided stat
#   _2s suffix refers to two-sided stat


import argparse
import re
import fnmatch
from pathlib import Path

from nilearn import plotting

try:
    from nilearn.glm import threshold_stats_img as _threshold_stats_img
except ImportError:
    _threshold_stats_img = None

try:
    from nilearn.glm import cluster_level_inference as _cluster_level_inference
except ImportError:
    _cluster_level_inference = None

try:
    from nilearn.reporting import get_clusters_table as _get_clusters_table
except ImportError:
    _get_clusters_table = None

try:
    from nilearn.image import resample_to_img as _resample_to_img
except ImportError:
    _resample_to_img = None

try:
    from scipy import stats as sp_stats
except Exception:
    sp_stats = None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def extract_entity(pattern, text):
    m = re.search(pattern, text)
    return m.group(1) if m else None


def parse_mapinfo(path, node=None):
    s = path.name
    sub      = extract_entity(r"(sub-[^_]+)", s)
    task     = extract_entity(r"(task-[^_]+)", s)
    run      = extract_entity(r"(run-[^_]+)", s)
    contrast = extract_entity(r"contrast-(.+?)_stat-", s)
    if contrast is not None:
        contrast = "contrast-" + contrast
    stat = extract_entity(r"(stat-[^_]+)", s)

    def strip(prefix, val):
        return val.replace(prefix, "") if val else None

    return {
        "path": path,
        "node": node,
        "sub":      strip("sub-",      sub),
        "task":     strip("task-",     task),
        "run":      strip("run-",      run),
        "contrast": strip("contrast-", contrast),
        "stat":     strip("stat-",     stat),
    }


def ok_filter(val, allowed_set):
    if allowed_set is None or len(allowed_set) == 0:
        return True
    return val in allowed_set


def match_filters(mi, subjects, tasks, runs, contrasts, stats_allowed):
    return (
        ok_filter(mi["sub"],      subjects)
        and ok_filter(mi["task"],     tasks)
        and ok_filter(mi["run"],      runs)
        and ok_filter(mi["contrast"], contrasts)
        and ok_filter(mi["stat"],     stats_allowed)
    )


def discover_maps(root, nodes, glob_pattern):
    out = []
    for node in nodes:
        node_dir = root / node
        if not node_dir.exists():
            continue
        if "**" in glob_pattern:
            name_pat = glob_pattern.split("/")[-1]
            for p in node_dir.rglob("*"):
                if p.is_file() and (p.name.endswith(".nii") or p.name.endswith(".nii.gz")):
                    if fnmatch.fnmatch(p.name, name_pat):
                        out.append(parse_mapinfo(p, node=node))
        else:
            for p in node_dir.glob(glob_pattern):
                if p.is_file() and (p.name.endswith(".nii") or p.name.endswith(".nii.gz")):
                    out.append(parse_mapinfo(p, node=node))
    return out


# ---------------------------------------------------------------------------
# Degree-of-freedom inference (for p-unc on t-maps)
# ---------------------------------------------------------------------------

def find_design_matrix_nearby(statmap_path):
    """Walk up the directory tree looking for a design matrix TSV."""
    here = statmap_path.parent
    for _ in range(6):
        for pat in ("*design_matrix*.tsv", "*design*.tsv", "*designmatrix*.tsv"):
            candidates = sorted(here.glob(pat))
            if candidates:
                return candidates[0]
        reports_sub = here / "reports"
        if reports_sub.is_dir():
            for pat in ("*design_matrix*.tsv", "*design*.tsv"):
                candidates = sorted(reports_sub.glob(pat))
                if candidates:
                    return candidates[0]
        if here == here.parent:
            break
        here = here.parent
    return None


def infer_df_from_lower_level(mi, root):
    """Estimate df for group-level maps by counting lower-level input statmaps."""
    contrast = mi.get("contrast")
    if not contrast:
        return None
    stat = mi.get("stat") or "t"
    current_node = "node-" + (mi.get("node") or "")
    try:
        all_nodes = [d for d in root.iterdir() if d.is_dir() and d.name.startswith("node-")]
    except OSError:
        return None
    run_nodes = [d for d in all_nodes if "run" in d.name.lower() and d.name != current_node]
    if not run_nodes:
        run_nodes = [d for d in all_nodes if d.name != current_node]
    if not run_nodes:
        return None
    pat  = "*contrast-{}*stat-{}*statmap.nii*".format(contrast, stat)
    sub  = mi.get("sub")
    task = mi.get("task")
    n = 0
    for node_dir in run_nodes:
        for candidate in node_dir.rglob(pat):
            name = candidate.name
            if sub  and "sub-{}".format(sub)   not in name: continue
            if task and "task-{}".format(task) not in name: continue
            n += 1
    return max(n - 1, 1) if n > 0 else None


def infer_df_from_design(design_tsv):
    try:
        import pandas as pd
        X = pd.read_csv(design_tsv, sep="\t")
        n_tp, n_reg = X.shape
        return int(max(n_tp - n_reg, 1))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Thresholding
# ---------------------------------------------------------------------------

def threshold_t_from_p(p_unc, df, two_sided):
    if sp_stats is None:
        raise RuntimeError("scipy is required for p-unc thresholding.")
    if two_sided:
        return float(sp_stats.t.isf(p_unc / 2.0, df))
    return float(sp_stats.t.isf(p_unc, df))


def threshold_z_from_p(p_unc, two_sided):
    if sp_stats is None:
        raise RuntimeError("scipy is required for p-unc thresholding.")
    if two_sided:
        return float(sp_stats.norm.isf(p_unc / 2.0))
    return float(sp_stats.norm.isf(p_unc))


def load_roi_mask(roi_mask_path, stat_img_path):
    """Load and resample ROI mask to statmap space."""
    if roi_mask_path is None:
        return None
    if _resample_to_img is None:
        raise RuntimeError("nilearn.image.resample_to_img not available; cannot apply --roi-mask.")
    import nibabel as nib
    mask = nib.load(str(roi_mask_path))
    ref  = nib.load(str(stat_img_path))
    resampled = _resample_to_img(mask, ref, interpolation="nearest")
    return resampled


def apply_threshold_stats_img(stat_img_path, height_control, alpha, cluster_extent,
                               two_sided, roi_mask_img):
    """
    Apply FDR or Bonferroni correction via nilearn.glm.threshold_stats_img.

    Returns (thresholded_img, threshold_value).
    Requires z-maps; emits a warning if called on t-maps (detected from filename).
    """
    if _threshold_stats_img is None:
        raise RuntimeError("nilearn.glm.threshold_stats_img not available.")
    import nibabel as nib
    img = nib.load(str(stat_img_path))
    thresholded, thr_value = _threshold_stats_img(
        img,
        mask_img=roi_mask_img,
        alpha=alpha,
        height_control=height_control,
        cluster_threshold=int(cluster_extent) if cluster_extent is not None else 0,
        two_sided=two_sided,
    )
    return thresholded, float(thr_value)


def apply_cluster_extent_only(stat_img_path, thr_value, cluster_extent, two_sided):
    """
    Apply a pre-computed voxel threshold then remove clusters < cluster_extent voxels.
    Used for p-unc and fixed modes where the threshold is computed externally.
    """
    if _threshold_stats_img is None:
        print("[WARN] nilearn.glm.threshold_stats_img not available — skipping cluster extent filter")
        return None
    if thr_value is None:
        return None
    import nibabel as nib
    img = nib.load(str(stat_img_path))
    thresholded, _ = _threshold_stats_img(
        img,
        threshold=float(thr_value),
        height_control=None,
        cluster_threshold=int(cluster_extent),
        two_sided=False,   # sign already handled by thr_value
    )
    return thresholded


def apply_roi_mask_to_img(stat_img_path, thr_value, roi_mask_img):
    """
    For p-unc and fixed modes: zero out voxels outside the ROI mask after thresholding.
    This is the SVC spatial restriction without adjusting the correction denominator.
    """
    if roi_mask_img is None:
        return None
    import nibabel as nib
    import numpy as np
    img  = nib.load(str(stat_img_path))
    data = img.get_fdata().copy()
    mask_data = roi_mask_img.get_fdata()
    data[mask_data == 0] = 0.0
    if thr_value is not None:
        data[np.abs(data) < thr_value] = 0.0
    return nib.Nifti1Image(data, img.affine, img.header)


def apply_ari(stat_img_path, ari_thresholds, alpha, roi_mask_img):
    """
    All-Resolution Inference (Rosenblatt et al. 2018).
    Returns a proportion-of-true-discoveries NIfTI image (values 0–1).
    """
    if _cluster_level_inference is None:
        raise RuntimeError("nilearn.glm.cluster_level_inference not available.")
    import nibabel as nib
    img = nib.load(str(stat_img_path))
    kwargs = dict(threshold=ari_thresholds, alpha=alpha)
    if roi_mask_img is not None:
        kwargs["mask_img"] = roi_mask_img
    proportion_img = _cluster_level_inference(img, **kwargs)
    return proportion_img


# ---------------------------------------------------------------------------
# Cluster table
# ---------------------------------------------------------------------------

def write_cluster_table(stat_img_or_path, thr_value, cluster_extent, two_sided, outpath):
    """Write a TSV cluster table via nilearn.reporting.get_clusters_table."""
    if _get_clusters_table is None:
        print("[WARN] nilearn.reporting.get_clusters_table not available — skipping cluster table")
        return
    if thr_value is None:
        return
    try:
        table = _get_clusters_table(
            stat_img_or_path,
            stat_threshold=float(thr_value),
            cluster_threshold=int(cluster_extent) if cluster_extent is not None else 0,
            two_sided=two_sided,
        )
        table.to_csv(str(outpath), sep="\t", index=False)
    except Exception as e:
        print("[WARN] cluster table failed: {}".format(e))


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def build_tag(mi):
    parts = []
    if mi["node"]:     parts.append("node-{}".format(mi["node"]))
    if mi["sub"]:      parts.append("sub-{}".format(mi["sub"]))
    if mi["task"]:     parts.append("task-{}".format(mi["task"]))
    if mi["run"]:      parts.append("run-{}".format(mi["run"]))
    if mi["contrast"]: parts.append("contrast-{}".format(mi["contrast"]))
    if mi["stat"]:     parts.append("stat-{}".format(mi["stat"]))
    return "__".join(parts)


def plot_one(mi, outdir, mode, cut_coords, thr, plot_abs, vmax, stat_img=None):
    tag = build_tag(mi)
    out_glass  = outdir / (tag + "__glass.png")
    out_slices = outdir / (tag + "__slices.png")

    img_src  = stat_img if stat_img is not None else str(mi["path"])
    plot_thr = 0 if stat_img is not None else thr

    if plot_abs and stat_img is None:
        import nibabel as nib
        import numpy as np
        raw = nib.load(str(mi["path"]))
        data = np.abs(raw.get_fdata())
        img_src = nib.Nifti1Image(data, raw.affine, raw.header)

    disp1 = plotting.plot_glass_brain(
        img_src, threshold=plot_thr, display_mode="lzry",
        colorbar=True, title=tag, vmax=vmax,
    )
    disp1.savefig(str(out_glass))
    disp1.close()

    disp2 = plotting.plot_stat_map(
        img_src, threshold=plot_thr, display_mode=mode,
        cut_coords=cut_coords, colorbar=True, title=tag, vmax=vmax,
    )
    disp2.savefig(str(out_slices))
    disp2.close()

    return out_glass, out_slices


def plot_one_ari(mi, outdir, mode, cut_coords, proportion_img):
    """Plot a proportion-of-true-discoveries (ARI) image."""
    tag = build_tag(mi) + "__ari"
    out_slices = outdir / (tag + "__slices.png")

    disp = plotting.plot_stat_map(
        proportion_img, threshold=0.0, display_mode=mode,
        cut_coords=cut_coords, colorbar=True, title=tag,
        vmax=1.0, cmap="inferno",
    )
    disp.savefig(str(out_slices))
    disp.close()

    # Glass brain with proportion map
    out_glass = outdir / (tag + "__glass.png")
    disp2 = plotting.plot_glass_brain(
        proportion_img, threshold=0.0, display_mode="lzry",
        colorbar=True, title=tag, vmax=1.0, cmap="inferno",
    )
    disp2.savefig(str(out_glass))
    disp2.close()

    return out_glass, out_slices


def plot_one_3d(mi, outdir, thr, vmax, stat_img=None, ari=False):
    """Generate an interactive 3D HTML viewer via nilearn.plotting.view_img."""
    try:
        from nilearn.plotting import view_img
    except ImportError:
        print("[WARN] nilearn.plotting.view_img not available — skipping 3D view")
        return None

    tag = build_tag(mi)
    suffix = "__ari__3d.html" if ari else "__3d.html"
    out_html = outdir / (tag + suffix)

    img_src  = stat_img if stat_img is not None else str(mi["path"])
    plot_thr = 0 if stat_img is not None else thr
    vmax_arg = 1.0 if ari else vmax

    view = view_img(img_src, threshold=plot_thr, vmax=vmax_arg, title=tag)
    view.save_as_html(str(out_html))

    return out_html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Statmap plotting utility for FitLins (or any BIDS-ish) derivatives."
    )
    ap.add_argument("--root",   type=Path, required=True)
    ap.add_argument("--nodes",  type=str, nargs="+", default=["node-runLevel"])
    ap.add_argument("--glob",   type=str, default="**/*stat-*.nii*")
    ap.add_argument("--outdir", type=Path, required=True)

    ap.add_argument("--subjects",  type=str, nargs="*", default=None)
    ap.add_argument("--tasks",     type=str, nargs="*", default=None)
    ap.add_argument("--runs",      type=str, nargs="*", default=None)
    ap.add_argument("--contrasts", type=str, nargs="*", default=None)
    ap.add_argument("--stats",     type=str, nargs="*", default=["t"])

    # Thresholding
    ap.add_argument(
        "--thr-mode",
        choices=["none", "fixed", "p-unc", "fdr", "bonferroni", "ari"],
        default="p-unc",
        help=(
            "Thresholding strategy. "
            "none: no threshold. "
            "fixed: fixed value (--thr-fixed). "
            "p-unc: uncorrected p-value (--p-unc); works for t and z maps. "
            "fdr: FDR voxel-level correction (--alpha); requires z-maps. "
            "bonferroni: Bonferroni FWER correction (--alpha); requires z-maps. "
            "ari: All-Resolution Inference, produces proportion-of-true-discoveries map "
            "(--alpha, --ari-thresholds)."
        ),
    )
    ap.add_argument("--thr-fixed", type=float, default=3.1,
                    help="Threshold value for --thr-mode fixed.")
    ap.add_argument("--p-unc",  type=float, default=0.001,
                    help="Uncorrected p-value for --thr-mode p-unc.")
    ap.add_argument("--alpha",  type=float, default=0.05,
                    help="Alpha level for --thr-mode fdr, bonferroni, or ari.")
    ap.add_argument("--two-sided", action="store_true",
                    help="Two-sided thresholding (splits alpha across both tails).")
    ap.add_argument("--df", type=int, default=None,
                    help="Degrees of freedom override for p-unc on t-maps.")

    # Cluster options
    ap.add_argument(
        "--cluster-extent", type=int, default=None, metavar="K",
        help=(
            "Minimum cluster size in voxels after voxel-level thresholding. "
            "Applies to all modes except none and ari. "
            "Example: --cluster-extent 10."
        ),
    )
    ap.add_argument(
        "--ari-thresholds", type=float, nargs="+", default=[2.5, 3.0, 3.5],
        help=(
            "Cluster-forming z-thresholds for ARI (--thr-mode ari). "
            "Multiple values produce a more stable estimate. Default: 2.5 3.0 3.5."
        ),
    )

    # SVC
    ap.add_argument(
        "--roi-mask", type=Path, default=None,
        help=(
            "NIfTI mask for Small Volume Correction. "
            "For fdr/bonferroni: restricts the correction denominator to the ROI "
            "(passed as mask_img to threshold_stats_img). "
            "For p-unc/fixed: zeros out voxels outside the mask after thresholding. "
            "Mask is resampled to statmap space automatically."
        ),
    )

    # Cluster table
    ap.add_argument(
        "--cluster-table", action="store_true",
        help="Write a TSV cluster summary table alongside each figure.",
    )

    # Rendering
    ap.add_argument("--display-mode", choices=["ortho", "x", "y", "z"], default="ortho")
    ap.add_argument("--cut-coords", type=float, nargs="*", default=None)
    ap.add_argument("--plot-abs",   action="store_true")
    ap.add_argument("--vmax",       type=float, default=None)
    ap.add_argument(
        "--view3d", action="store_true", default=False,
        help="Generate interactive 3D HTML viewers via nilearn.plotting.view_img.",
    )

    args = ap.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    subjects      = set(args.subjects)   if args.subjects   else None
    tasks         = set(args.tasks)      if args.tasks       else None
    runs          = set(args.runs)       if args.runs        else None
    contrasts     = set(args.contrasts)  if args.contrasts   else None
    stats_allowed = set(args.stats)      if args.stats       else None

    maps = discover_maps(args.root, args.nodes, args.glob)
    maps = [mi for mi in maps if match_filters(mi, subjects, tasks, runs, contrasts, stats_allowed)]
    maps.sort(key=lambda m: (
        m.get("node") or "", m.get("sub") or "", m.get("task") or "",
        m.get("run") or "", m.get("contrast") or "", m.get("stat") or "",
        m["path"].name,
    ))

    if len(maps) == 0:
        raise SystemExit("No maps matched your filters. Adjust --glob/--nodes/filters.")

    manifest = args.outdir / "manifest.tsv"
    with manifest.open("w") as f:
        f.write(
            "node\tsub\ttask\trun\tcontrast\tstat\tmap_path\t"
            "thr_mode\tthr_value\tcluster_extent\troi_mask\t"
            "glass_png\tslices_png\tview3d_html\tcluster_table\n"
        )

        for mi in maps:
            stat_kind = (mi.get("stat") or "").lower()
            path      = mi["path"]

            # Load ROI mask once per map (resampled to statmap space)
            roi_mask_img = None
            if args.roi_mask is not None:
                roi_mask_img = load_roi_mask(args.roi_mask, path)

            thr_value   = None
            thr_mode    = args.thr_mode
            cluster_img = None
            ari_img     = None

            # ------------------------------------------------------------------
            # Thresholding
            # ------------------------------------------------------------------

            if args.thr_mode == "none":
                thr_value = None

            elif args.thr_mode == "fixed":
                thr_value = float(args.thr_fixed)
                if roi_mask_img is not None:
                    cluster_img = apply_roi_mask_to_img(path, thr_value, roi_mask_img)
                if args.cluster_extent is not None and thr_value is not None:
                    src = cluster_img if cluster_img is not None else path
                    cluster_img = apply_cluster_extent_only(src, thr_value, args.cluster_extent, args.two_sided)
                    thr_mode += "_k{}".format(args.cluster_extent)

            elif args.thr_mode == "p-unc":
                if stat_kind == "z":
                    thr_value = threshold_z_from_p(args.p_unc, two_sided=args.two_sided)
                    thr_mode  = "p-unc({})_{}_z".format(args.p_unc, "2s" if args.two_sided else "1s")
                else:
                    # t-map: infer df
                    df = args.df
                    if df is None:
                        design = find_design_matrix_nearby(path)
                        if design is not None:
                            df = infer_df_from_design(design)
                    if df is None:
                        df = infer_df_from_lower_level(mi, args.root)
                        if df is not None:
                            print("[df-auto] {}: inferred df={}".format(path.name, df))
                    if df is None:
                        raise RuntimeError(
                            "Cannot infer df for p-unc thresholding.\n"
                            "  Map: {}\n"
                            "  Fix: pass --df <value> on the CLI.".format(path)
                        )
                    thr_value = threshold_t_from_p(args.p_unc, df=df, two_sided=args.two_sided)
                    thr_mode  = "p-unc({})_{}_t_df{}".format(
                        args.p_unc, "2s" if args.two_sided else "1s", df)

                if roi_mask_img is not None:
                    cluster_img = apply_roi_mask_to_img(path, thr_value, roi_mask_img)
                if args.cluster_extent is not None and thr_value is not None:
                    src = cluster_img if cluster_img is not None else path
                    cluster_img = apply_cluster_extent_only(src, thr_value, args.cluster_extent, args.two_sided)
                    thr_mode += "_k{}".format(args.cluster_extent)

            elif args.thr_mode in ("fdr", "bonferroni"):
                if stat_kind == "t":
                    print(
                        "[WARN] {} correction expects z-maps; '{}' appears to be a t-map. "
                        "The threshold will be in z-scale applied to t-values and is likely "
                        "misscaled. Consider using z-statmaps (group level) for this mode.".format(
                            args.thr_mode, path.name)
                    )
                try:
                    cluster_img, thr_value = apply_threshold_stats_img(
                        path,
                        height_control=args.thr_mode,
                        alpha=args.alpha,
                        cluster_extent=args.cluster_extent,
                        two_sided=args.two_sided,
                        roi_mask_img=roi_mask_img,
                    )
                    svc_tag = "_svc" if roi_mask_img is not None else ""
                    k_tag   = "_k{}".format(args.cluster_extent) if args.cluster_extent else ""
                    thr_mode = "{}_alpha{}_{}{}{}_{}".format(
                        args.thr_mode, args.alpha,
                        "2s" if args.two_sided else "1s",
                        svc_tag, k_tag, stat_kind,
                    )
                except Exception as e:
                    print("[WARN] {}: {} correction failed ({}); falling back to none.".format(
                        path.name, args.thr_mode, e))
                    thr_value   = None
                    cluster_img = None

            elif args.thr_mode == "ari":
                try:
                    ari_img  = apply_ari(path, args.ari_thresholds, args.alpha, roi_mask_img)
                    thr_mode = "ari_alpha{}_thr{}{}".format(
                        args.alpha,
                        "-".join(str(t) for t in args.ari_thresholds),
                        "_svc" if roi_mask_img is not None else "",
                    )
                    thr_value = None   # ARI has no single threshold
                except Exception as e:
                    print("[WARN] {}: ARI failed ({}); skipping.".format(path.name, e))
                    ari_img = None

            # ------------------------------------------------------------------
            # Plotting
            # ------------------------------------------------------------------

            view3d_html  = None
            cluster_table_path = None

            if ari_img is not None:
                glass_png, slices_png = plot_one_ari(
                    mi, args.outdir, args.display_mode, args.cut_coords, ari_img,
                )
                if args.view3d:
                    view3d_html = plot_one_3d(
                        mi, args.outdir, thr=None, vmax=1.0, stat_img=ari_img, ari=True,
                    )

            else:
                glass_png, slices_png = plot_one(
                    mi=mi, outdir=args.outdir, mode=args.display_mode,
                    cut_coords=args.cut_coords, thr=thr_value, plot_abs=args.plot_abs,
                    vmax=args.vmax, stat_img=cluster_img,
                )
                if args.view3d:
                    view3d_html = plot_one_3d(
                        mi=mi, outdir=args.outdir, thr=thr_value,
                        vmax=args.vmax, stat_img=cluster_img,
                    )
                if args.cluster_table and thr_value is not None:
                    tag = build_tag(mi)
                    cluster_table_path = args.outdir / (tag + "__clusters.tsv")
                    src = cluster_img if cluster_img is not None else str(path)
                    effective_thr = 0.0 if cluster_img is not None else thr_value
                    write_cluster_table(
                        src, effective_thr, None, args.two_sided, cluster_table_path,
                    )

            # ------------------------------------------------------------------
            # Manifest row
            # ------------------------------------------------------------------
            f.write("{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\n".format(
                mi.get("node") or "",
                mi.get("sub")  or "",
                mi.get("task") or "",
                mi.get("run")  or "",
                mi.get("contrast") or "",
                mi.get("stat")     or "",
                str(path),
                thr_mode,
                "" if thr_value is None else "{:.6g}".format(thr_value),
                "" if args.cluster_extent is None else str(args.cluster_extent),
                str(args.roi_mask) if args.roi_mask else "",
                str(glass_png),
                str(slices_png),
                "" if view3d_html is None else str(view3d_html),
                "" if cluster_table_path is None else str(cluster_table_path),
            ))

    print("[OK] Wrote {} figure-set(s) to {}".format(len(maps), args.outdir))
    print("[OK] Manifest: {}".format(manifest))


if __name__ == "__main__":
    main()