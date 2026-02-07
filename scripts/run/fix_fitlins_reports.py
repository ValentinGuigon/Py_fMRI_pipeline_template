#!/usr/bin/env python3
"""
fix_fitlins_reports.py
=====================

Post-hoc HTML patcher for FitLins reports.

PURPOSE
-------
This script fixes *presentation-only* issues in FitLins HTML reports
without touching any modeling outputs. It addresses two recurring problems:

1) Broken image/link paths in reports generated inside containers
   (e.g. paths starting with /work/, ../work/, or absolute /node-runLevel/...).

2) Spurious "Missing contrast skipped (--drop-missing)" messages in the report
   when contrast images actually exist on disk. In those cases, the script
   locates available *_ortho.png images and embeds them directly into the HTML.

This script is SAFE:
- It does NOT modify NIfTIs, TSVs, or model outputs.
- It only rewrites the HTML report in place (with a .bak backup).

USAGE
-----
Basic:
    python fix_fitlins_reports.py /path/to/model-<MODELNAME>.html

Verbose (prints missing-figure diagnostics):
    python fix_fitlins_reports.py /path/to/model-<MODELNAME>.html --verbose

EXPECTED LOCATION
-----------------
The report must live at:
    <derivatives_root>/reports/<report>.html

The script infers <derivatives_root> automatically and rewrites paths
relative to <derivatives_root>/reports/.

WHAT GETS FIXED
---------------
Handled src/href patterns:
  A) /work/<deriv_name>/...              -> ../...
  B) ../work/<deriv_name>/...            -> ../...
  C) ./work/<deriv_name>/...             -> ../...
  D) /node-runLevel/... or /reports/...  -> ../node-runLevel/...

External links (http, https), data URIs, and anchors (#) are left untouched.

CONTRAST PATCHING
-----------------
If the report contains:
    "Missing contrast skipped (used: --drop-missing)"

The script searches for:
    node-*/reports/sub-XXX/figures/*_ortho.png

and injects any found images directly into the report under that section.

AUTHOR INTENT
-------------
This exists because FitLins output is correct, but its HTML report is fragile
outside the container context. This script makes reports portable and readable.

Do not use this as part of modeling. Use it only after FitLins completes.
"""

from pathlib import Path
import re
import sys


def derivatives_root_from_report(report_path):
    # Assumes: <deriv>/reports/<report>.html
    return report_path.parent.parent


def rewrite_src_href_paths(html, report_path):
    """
    Rewrite src/href targets so they resolve from <deriv>/reports/.

    Handles:
      A) container absolute: /work/<deriv_name>/...
         -> ../...

      B) container-relative with work prefix: ../work/<deriv_name>/... or ./work/<deriv_name>/...
         -> ../...

      C) root-absolute within derivatives tree: /node-runLevel/... or /reports/...
         -> ../node-runLevel/... etc.

    Leaves http(s), data:, and # unchanged.
    """
    deriv_root = derivatives_root_from_report(report_path).resolve()
    deriv_name = deriv_root.name

    def normalize(val):
        # leave external / anchors untouched
        if val.startswith(("http://", "https://", "data:", "#")):
            return val

        # Helper: strip a known prefix and map to ../<tail>
        def strip_to_reports(prefix):
            if val.startswith(prefix):
                tail = val[len(prefix):]
                return "../" + tail
            return None

        # B) container-relative with work prefix (old style): ../work/<deriv_name>/...
        for pref in ("../work/", "./work/"):
            if val.startswith(pref):
                # NEW: ../work/fitlins_derivatives/<deriv_name>/...
                out = strip_to_reports(pref + "fitlins_derivatives/" + deriv_name + "/")
                if out is not None:
                    return out
                # Old: ../work/<deriv_name>/...
                out = strip_to_reports(pref + deriv_name + "/")
                if out is not None:
                    return out
                return val

        # A) container absolute (old style): /work/<deriv_name>/...
        if val.startswith("/work/"):
            # NEW: /work/fitlins_derivatives/<deriv_name>/...
            out = strip_to_reports("/work/fitlins_derivatives/" + deriv_name + "/")
            if out is not None:
                return out
            # Old: /work/<deriv_name>/...
            out = strip_to_reports("/work/" + deriv_name + "/")
            if out is not None:
                return out
            return val

        # C) root-absolute within derivatives tree: /node-runLevel/... -> ../node-runLevel/...
        if val.startswith("/"):
            return "../" + val.lstrip("/")

        return val

    def repl(m):
        attr, val = m.group(1), m.group(2)
        new_val = normalize(val)
        return m.group(0) if new_val == val else f'{attr}="{new_val}"'

    return re.sub(r'\b(src|href)="([^"]+)"', repl, html, flags=re.IGNORECASE)


def find_ortho_pngs(deriv_root, subj, task, limit=8):
    out = []
    subj_dir = f"sub-{subj}"

    for node_dir in deriv_root.glob("node-*"):
        figdir = node_dir / "reports" / subj_dir / "figures"
        if figdir.exists():
            out.extend(figdir.glob(f"sub-{subj}_task-{task}_*_ortho.png"))

    out = sorted(set(out), key=lambda p: (("stat-t" not in p.name), p.name))
    return out[:limit]


def rel_from_reports(report_path, target_path):
    deriv_root = derivatives_root_from_report(report_path).resolve()
    rel = target_path.resolve().relative_to(deriv_root)
    return "../" + str(rel).replace("\\", "/")


def inject_missing_contrast_blocks(html, report_path, verbose=False):
    deriv_root = derivatives_root_from_report(report_path)

    pat = re.compile(
        r'(Subject:\s*(\d+)\s*,\s*Task:\s*([A-Za-z0-9]+).*?'
        r'Missing contrast skipped\s*\(used:\s*<code>--drop-missing</code>\)\s*</p>)',
        flags=re.DOTALL,
    )

    pieces, last, n = [], 0, 0

    for m in pat.finditer(html):
        pieces.append(html[last:m.end()])
        subj, task = m.group(2), m.group(3)

        figs = find_ortho_pngs(deriv_root, subj, task)
        if figs:
            block = ["\n        <div class=\"reportlet\">\n",
                     "          <p>Contrast figures found on disk (embedded via post-hoc patch):</p>\n"]
            for fp in figs:
                rel = rel_from_reports(report_path, fp)
                block += [
                    "          <div style=\"margin: 8px 0;\">\n",
                    f"            <div style=\"font-family: monospace; font-size: 12px;\">{fp.name}</div>\n",
                    f"            <img src=\"{rel}\" style=\"max-width: 100%; height: auto;\" />\n",
                    "          </div>\n",
                ]
            block.append("        </div>\n")
            pieces.append("".join(block))
            n += 1
        elif verbose:
            sys.stderr.write(f"No ortho PNGs found for sub-{subj} task-{task}\n")

        last = m.end()

    pieces.append(html[last:])
    return "".join(pieces), n


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("Usage: fix_fitlins_reports.py <report.html> [--verbose]\n")
        return 2

    report = Path(argv[1])
    verbose = "--verbose" in argv

    if not report.exists():
        sys.stderr.write(f"ERROR: report not found: {report}\n")
        return 1

    bak = report.with_suffix(report.suffix + ".bak")
    if not bak.exists():
        bak.write_text(report.read_text(errors="ignore"), errors="ignore")

    html = report.read_text(errors="ignore")
    html = rewrite_src_href_paths(html, report)
    html, n_patched = inject_missing_contrast_blocks(html, report, verbose)
    report.write_text(html)

    print("Patched in place:", report)
    print("Backup:", bak)
    print("Patched missing-contrast blocks:", n_patched)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
