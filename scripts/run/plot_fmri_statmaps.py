#!/usr/bin/env python3
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
    from scipy import stats as sp_stats
except Exception:
    sp_stats = None


def extract_entity(pattern, text):
    m = re.search(pattern, text)
    return m.group(1) if m else None


def parse_mapinfo(path, node=None):
    s = path.name

    sub = extract_entity(r"(sub-[^_]+)", s)
    task = extract_entity(r"(task-[^_]+)", s)
    run = extract_entity(r"(run-[^_]+)", s)

    contrast = extract_entity(r"contrast-(.+?)_stat-", s)
    if contrast is not None:
        contrast = "contrast-" + contrast

    stat = extract_entity(r"(stat-[^_]+)", s)

    def strip(prefix, val):
        return val.replace(prefix, "") if val else None

    return {
        "path": path,
        "node": node,
        "sub": strip("sub-", sub),
        "task": strip("task-", task),
        "run": strip("run-", run),
        "contrast": strip("contrast-", contrast),
        "stat": strip("stat-", stat),
    }


def ok_filter(val, allowed_set):
    if allowed_set is None or len(allowed_set) == 0:
        return True
    return val in allowed_set


def match_filters(mi, subjects, tasks, runs, contrasts, stats_allowed):
    return (
        ok_filter(mi["sub"], subjects)
        and ok_filter(mi["task"], tasks)
        and ok_filter(mi["run"], runs)
        and ok_filter(mi["contrast"], contrasts)
        and ok_filter(mi["stat"], stats_allowed)
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


def find_design_matrix_nearby(statmap_path):
    """Walk up the directory tree looking for a design matrix TSV.

    Also checks a reports/ subdirectory at each level, which is where
    FitLins sometimes writes design matrices alongside figures.
    Walks up to 6 levels (enough to reach the node root from a deeply
    nested subject/task/run directory).
    """
    here = statmap_path.parent
    for _ in range(6):
        for pat in ("*design_matrix*.tsv", "*design*.tsv", "*designmatrix*.tsv"):
            candidates = sorted(here.glob(pat))
            if candidates:
                return candidates[0]
        # FitLins sometimes stores design matrices inside a reports/ subdir
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
    """Estimate df for group-level maps by counting lower-level input statmaps.

    Group-level nodes use a one-sample t-test over their inputs (X=[1],
    DummyContrasts), so df = n_inputs - 1.  We approximate n_inputs by
    counting run-level statmaps that match the same contrast, optionally
    filtered by subject and task when those entities are present in the
    current map (i.e. for conditionLevel or subjectLevel nodes).

    Falls back gracefully if the run-level node directory cannot be found.
    """
    contrast = mi.get("contrast")
    if not contrast:
        return None

    stat = mi.get("stat") or "t"
    current_node = "node-" + (mi.get("node") or "")

    # Locate candidate lower-level node directories.
    # Prefer the explicit run-level node; accept any other node as fallback.
    try:
        all_nodes = [d for d in root.iterdir() if d.is_dir() and d.name.startswith("node-")]
    except OSError:
        return None

    run_nodes = [d for d in all_nodes if "run" in d.name.lower() and d.name != current_node]
    if not run_nodes:
        run_nodes = [d for d in all_nodes if d.name != current_node]
    if not run_nodes:
        return None

    pat = "*contrast-{}*stat-{}*statmap.nii*".format(contrast, stat)
    sub  = mi.get("sub")
    task = mi.get("task")

    n = 0
    for node_dir in run_nodes:
        for candidate in node_dir.rglob(pat):
            name = candidate.name
            if sub  and "sub-{}".format(sub)   not in name:
                continue
            if task and "task-{}".format(task) not in name:
                continue
            n += 1

    return max(n - 1, 1) if n > 0 else None


def infer_df_from_design(design_tsv):
    try:
        import pandas as pd
        X = pd.read_csv(design_tsv, sep="\t")
        n_tp, n_reg = X.shape
        df = int(max(n_tp - n_reg, 1))
        return df
    except Exception:
        return None


def threshold_t_from_p(p_unc, df, two_sided):
    if sp_stats is None:
        raise RuntimeError("scipy is required for p-based thresholding but is not available.")
    if two_sided:
        return float(sp_stats.t.isf(p_unc / 2.0, df))
    return float(sp_stats.t.isf(p_unc, df))


def threshold_z_from_p(p_unc, two_sided):
    if sp_stats is None:
        raise RuntimeError("scipy is required for p-based thresholding but is not available.")
    if two_sided:
        return float(sp_stats.norm.isf(p_unc / 2.0))
    return float(sp_stats.norm.isf(p_unc))


def apply_cluster_extent(stat_img_path, thr_value, cluster_extent):
    """Apply voxel threshold then remove clusters smaller than cluster_extent voxels.

    Uses nilearn.glm.threshold_stats_img with height_control=None so the
    already-computed thr_value is used directly (no p-value re-computation).

    Returns a thresholded Nifti1Image, or None if the function is unavailable.
    """
    if _threshold_stats_img is None:
        print("[WARN] nilearn.glm.threshold_stats_img not available — skipping cluster extent filtering")
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
        two_sided=False,   # sign already handled by thr_value; we pass absolute threshold
    )
    return thresholded


def build_tag(mi):
    parts = []
    if mi["node"]: parts.append("node-{}".format(mi["node"]))
    if mi["sub"]: parts.append("sub-{}".format(mi["sub"]))
    if mi["task"]: parts.append("task-{}".format(mi["task"]))
    if mi["run"]: parts.append("run-{}".format(mi["run"]))
    if mi["contrast"]: parts.append("contrast-{}".format(mi["contrast"]))
    if mi["stat"]: parts.append("stat-{}".format(mi["stat"]))
    return "__".join(parts)


def plot_one(mi, outdir, mode, cut_coords, thr, plot_abs, vmax, stat_img=None):
    """Plot glass brain and slice view for one statmap.

    stat_img : optional pre-thresholded Nifti1Image (from cluster extent filtering).
               If None, the raw path mi["path"] is used and thr is applied by nilearn.
               If provided, threshold=0 so the already-masked image displays as-is.
    """
    tag = build_tag(mi)
    out_glass = outdir / (tag + "__glass.png")
    out_slices = outdir / (tag + "__slices.png")

    img_src = stat_img if stat_img is not None else str(mi["path"])
    plot_thr = 0 if stat_img is not None else thr

    disp1 = plotting.plot_glass_brain(
        img_src,
        threshold=plot_thr,
        display_mode="lyrz",
        plot_abs=plot_abs,
        colorbar=True,
        title=tag,
        vmax=vmax,
    )
    disp1.savefig(str(out_glass))
    disp1.close()

    disp2 = plotting.plot_stat_map(
        img_src,
        threshold=plot_thr,
        display_mode=mode,
        cut_coords=cut_coords,
        colorbar=True,
        title=tag,
        vmax=vmax,
    )
    disp2.savefig(str(out_slices))
    disp2.close()

    return out_glass, out_slices


def plot_one_3d(mi, outdir, thr, vmax, stat_img=None):
    """Generate an interactive 3D HTML viewer using nilearn.plotting.view_img.

    stat_img : optional pre-thresholded Nifti1Image (from cluster extent filtering).
               If None, the raw path mi["path"] is used.
    """
    try:
        from nilearn.plotting import view_img
    except ImportError:
        print("[WARN] nilearn.plotting.view_img not available — skipping 3D view")
        return None

    tag = build_tag(mi)
    out_html = outdir / (tag + "__3d.html")

    img_src = stat_img if stat_img is not None else str(mi["path"])
    plot_thr = 0 if stat_img is not None else thr

    view = view_img(
        img_src,
        threshold=plot_thr,
        vmax=vmax,
        title=tag,
    )
    view.save_as_html(str(out_html))

    return out_html


def main():
    ap = argparse.ArgumentParser(
        description="General statmap plotting utility for FitLins (or any BIDS-ish) derivatives."
    )
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--nodes", type=str, nargs="+", default=["node-runLevel"])
    ap.add_argument("--glob", type=str, default="**/*stat-*.nii*")
    ap.add_argument("--outdir", type=Path, required=True)

    ap.add_argument("--subjects", type=str, nargs="*", default=None)
    ap.add_argument("--tasks", type=str, nargs="*", default=None)
    ap.add_argument("--runs", type=str, nargs="*", default=None)
    ap.add_argument("--contrasts", type=str, nargs="*", default=None)
    ap.add_argument("--stats", type=str, nargs="*", default=["t"])

    ap.add_argument("--thr-mode", choices=["none", "fixed", "p-unc"], default="p-unc")
    ap.add_argument("--thr-fixed", type=float, default=3.1)
    ap.add_argument("--p-unc", type=float, default=0.001)
    ap.add_argument("--two-sided", action="store_true")
    ap.add_argument("--df", type=int, default=None)

    ap.add_argument("--display-mode", choices=["ortho", "x", "y", "z"], default="ortho")
    ap.add_argument("--cut-coords", type=float, nargs="*", default=None)
    ap.add_argument("--plot-abs", action="store_true")
    ap.add_argument("--vmax", type=float, default=None)
    ap.add_argument(
        "--cluster-extent", type=int, default=None, metavar="K",
        help="Minimum cluster size in voxels after voxel-level thresholding. "
             "Applied via nilearn.glm.threshold_stats_img. "
             "Example: --cluster-extent 10  (discard clusters < 10 voxels). "
             "Default: off (no cluster filtering)."
    )

    ap.add_argument(
        "--view3d", action="store_true", default=False,
        help="Generate an interactive 3D HTML viewer (_3d.html) for each statmap "
             "using nilearn.plotting.view_img. Output saved alongside glass/slices PNGs."
    )

    args = ap.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    subjects = set(args.subjects) if args.subjects else None
    tasks = set(args.tasks) if args.tasks else None
    runs = set(args.runs) if args.runs else None
    contrasts = set(args.contrasts) if args.contrasts else None
    stats_allowed = set(args.stats) if args.stats else None

    maps = discover_maps(args.root, args.nodes, args.glob)
    maps = [mi for mi in maps if match_filters(mi, subjects, tasks, runs, contrasts, stats_allowed)]
    maps.sort(key=lambda m: (m.get("node") or "", m.get("sub") or "", m.get("task") or "",
                             m.get("run") or "", m.get("contrast") or "", m.get("stat") or "",
                             m["path"].name))

    if len(maps) == 0:
        raise SystemExit("No maps matched your filters. Adjust --glob/--nodes/filters.")

    manifest = args.outdir / "manifest.tsv"
    with manifest.open("w") as f:
        f.write("node\tsub\ttask\trun\tcontrast\tstat\tmap_path\tthr_mode\tthr_value\tcluster_extent\tglass_png\tslices_png\tview3d_html\n")

        for mi in maps:
            thr_value = None
            thr_mode = args.thr_mode
            stat_kind = (mi.get("stat") or "").lower()

            if args.thr_mode == "none":
                thr_value = None

            elif args.thr_mode == "fixed":
                thr_value = float(args.thr_fixed)

            else:
                # p-unc: support t (needs df) and z (no df)
                if stat_kind == "z":
                    thr_value = threshold_z_from_p(args.p_unc, two_sided=args.two_sided)
                    thr_mode = "p-unc({})_{}_z".format(args.p_unc, "2s" if args.two_sided else "1s")

                elif stat_kind in ("t", ""):
                    df = args.df

                    if df is None:
                        # Stage 1: find design matrix co-located with the statmap
                        # (works for run-level nodes which have per-run GLM designs).
                        design = find_design_matrix_nearby(mi["path"])
                        if design is not None:
                            df = infer_df_from_design(design)

                    if df is None:
                        # Stage 2: for group-level nodes (meta/intercept model with no
                        # design matrix on disk), count the lower-level input statmaps.
                        # df = n_inputs - 1 for a one-sample t-test (X=[1]).
                        df = infer_df_from_lower_level(mi, args.root)
                        if df is not None:
                            print("[df-auto] {} : inferred df={} from lower-level map count".format(
                                mi["path"].name, df))

                    if df is None:
                        raise RuntimeError(
                            "Cannot infer df for p-unc thresholding.\n"
                            "  Map : {}\n"
                            "  Neither a design matrix TSV was found near the statmap,\n"
                            "  nor could lower-level input maps be counted.\n"
                            "  Fix : pass --df <value> on the CLI or set DF_OVERRIDE=<value> in your config.\n"
                            "  Tip : check that --root points to the FitLins derivatives directory\n"
                            "        and that the node-runLevel directory exists there.".format(mi["path"])
                        )

                    thr_value = threshold_t_from_p(args.p_unc, df=df, two_sided=args.two_sided)
                    thr_mode = "p-unc({})_{}_t_df{}".format(
                        args.p_unc, "2s" if args.two_sided else "1s", df)

                else:
                    thr_mode = "none(non_tz_stat)"
                    thr_value = None

            # Cluster extent filtering (optional)
            cluster_img = None
            if args.cluster_extent is not None and thr_value is not None:
                cluster_img = apply_cluster_extent(mi["path"], thr_value, args.cluster_extent)
                if cluster_img is not None:
                    thr_mode += "_k{}".format(args.cluster_extent)

            glass_png, slices_png = plot_one(
                mi=mi,
                outdir=args.outdir,
                mode=args.display_mode,
                cut_coords=args.cut_coords,
                thr=thr_value,
                plot_abs=args.plot_abs,
                vmax=args.vmax,
                stat_img=cluster_img,
            )

            view3d_html = None
            if args.view3d:
                view3d_html = plot_one_3d(
                    mi=mi,
                    outdir=args.outdir,
                    thr=thr_value,
                    vmax=args.vmax,
                    stat_img=cluster_img,
                )

            f.write(
                "{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\n".format(
                    mi.get("node") or "",
                    mi.get("sub") or "",
                    mi.get("task") or "",
                    mi.get("run") or "",
                    mi.get("contrast") or "",
                    mi.get("stat") or "",
                    str(mi["path"]),
                    thr_mode,
                    "" if thr_value is None else "{:.6g}".format(thr_value),
                    "" if args.cluster_extent is None else str(args.cluster_extent),
                    str(glass_png),
                    str(slices_png),
                    "" if view3d_html is None else str(view3d_html),
                )
            )

    print("[OK] Wrote {} figure-sets to {}".format(len(maps), args.outdir))
    print("[OK] Manifest: {}".format(manifest))


if __name__ == "__main__":
    main()