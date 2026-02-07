#!/usr/bin/env python3
import argparse
import re
import fnmatch
from pathlib import Path

from nilearn import plotting

try:
    from scipy import stats as sp_stats
except Exception:
    sp_stats = None


def extract_entity(pattern, text):
    m = re.search(pattern, text)
    return m.group(1) if m else None


def parse_mapinfo(path, node=None):
    """
    Parse FitLins-style filenames robustly.

    Critical fix:
    - Entities like contrast can contain underscores, so we must NOT use [^_]+.
    - Instead, capture entity values by anchoring to known separators (e.g., _stat-).
      Example filename fragment:
        ..._contrast-visual_gt_fixation_stat-t_statmap.nii.gz
    """
    s = path.name  # filename only

    # sub/task/run: safe to stop at next underscore
    sub = extract_entity(r"(sub-[^_]+)", s)
    task = extract_entity(r"(task-[^_]+)", s)
    run = extract_entity(r"(run-[^_]+)", s)

    # contrast: capture everything between "contrast-" and "_stat-"
    # (contrast names may contain underscores)
    contrast = extract_entity(r"contrast-(.+?)_stat-", s)
    if contrast is not None:
        contrast = "contrast-" + contrast

    # stat: capture "stat-<...>" up to next underscore
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
    here = statmap_path.parent
    for _ in range(4):
        for cand in here.glob("*design*tsv"):
            return cand
        for cand in here.glob("*design_matrix*tsv"):
            return cand
        here = here.parent
    return None


def infer_df_from_design(design_tsv):
    try:
        import pandas as pd
        X = pd.read_csv(design_tsv, sep="\t")
        n_tp, n_reg = X.shape
        df = int(max(n_tp - n_reg, 1))
        return df
    except Exception:
        return None


def threshold_from_p(p_unc, df, two_sided):
    if sp_stats is None:
        raise RuntimeError("scipy is required for p-based thresholding but is not available.")
    if two_sided:
        return float(sp_stats.t.isf(p_unc / 2.0, df))
    return float(sp_stats.t.isf(p_unc, df))


def build_tag(mi):
    parts = []
    if mi["node"]: parts.append("node-{}".format(mi["node"]))
    if mi["sub"]: parts.append("sub-{}".format(mi["sub"]))
    if mi["task"]: parts.append("task-{}".format(mi["task"]))
    if mi["run"]: parts.append("run-{}".format(mi["run"]))
    if mi["contrast"]: parts.append("contrast-{}".format(mi["contrast"]))
    if mi["stat"]: parts.append("stat-{}".format(mi["stat"]))
    return "__".join(parts)


def plot_one(mi, outdir, mode, cut_coords, thr, plot_abs, vmax):
    tag = build_tag(mi)
    out_glass = outdir / (tag + "__glass.png")
    out_slices = outdir / (tag + "__slices.png")

    disp1 = plotting.plot_glass_brain(
        str(mi["path"]),
        threshold=thr,
        display_mode="lyrz",
        plot_abs=plot_abs,
        colorbar=True,
        title=tag,
        vmax=vmax,
    )
    disp1.savefig(str(out_glass))
    disp1.close()

    disp2 = plotting.plot_stat_map(
        str(mi["path"]),
        threshold=thr,
        display_mode=mode,
        cut_coords=cut_coords,
        colorbar=True,
        title=tag,
        vmax=vmax,
    )
    disp2.savefig(str(out_slices))
    disp2.close()

    return out_glass, out_slices


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
        f.write("node\tsub\ttask\trun\tcontrast\tstat\tmap_path\tthr_mode\tthr_value\tglass_png\tslices_png\n")

        for mi in maps:
            thr_value = None
            thr_mode = args.thr_mode

            if args.thr_mode == "none":
                thr_value = None
            elif args.thr_mode == "fixed":
                thr_value = float(args.thr_fixed)
            else:
                # p-unc: only meaningful for t maps here
                if mi.get("stat") not in (None, "t"):
                    thr_mode = "none(non_t_stat)"
                    thr_value = None
                else:
                    df = args.df
                    if df is None:
                        design = find_design_matrix_nearby(mi["path"])
                        if design is not None:
                            df = infer_df_from_design(design)

                    if df is None:
                        thr_mode = "fixed_fallback"
                        thr_value = float(args.thr_fixed)
                    else:
                        thr_value = threshold_from_p(args.p_unc, df=df, two_sided=args.two_sided)
                        thr_mode = "p-unc({})_{}_df{}".format(args.p_unc, "2s" if args.two_sided else "1s", df)

            glass_png, slices_png = plot_one(
                mi=mi,
                outdir=args.outdir,
                mode=args.display_mode,
                cut_coords=args.cut_coords,
                thr=thr_value,
                plot_abs=args.plot_abs,
                vmax=args.vmax,
            )

            f.write(
                "{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\n".format(
                    mi.get("node") or "",
                    mi.get("sub") or "",
                    mi.get("task") or "",
                    mi.get("run") or "",
                    mi.get("contrast") or "",
                    mi.get("stat") or "",
                    str(mi["path"]),
                    thr_mode,
                    "" if thr_value is None else "{:.6g}".format(thr_value),
                    str(glass_png),
                    str(slices_png),
                )
            )

    print("[OK] Wrote {} figure-sets to {}".format(len(maps), args.outdir))
    print("[OK] Manifest: {}".format(manifest))


if __name__ == "__main__":
    main()
