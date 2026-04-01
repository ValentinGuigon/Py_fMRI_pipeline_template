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
   (e.g. paths starting with /slb_work/, ../slb_work/, or absolute /node-runLevel/...).

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
  A) <absolute-deriv-root>/...           -> ../...  (slb_works at any nesting depth)
  B) ../slb_work/<deriv_name>/...            -> ../...
  C) ./slb_work/<deriv_name>/...             -> ../...
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

import base64
import mimetypes
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
      A) Absolute path matching the actual derivatives root at any depth
         (covers flat layout fitlins_derivatives/<name>/ and namespaced
         layout fitlins_derivatives/tmth/<name>/ equally)
         -> ../...

      B) Container-relative with slb_work prefix: ../slb_work/<deriv_name>/... or
         ./slb_work/<deriv_name>/... (legacy fallback patterns)
         -> ../...

      C) Root-absolute within derivatives tree: /node-runLevel/... or /reports/...
         -> ../node-runLevel/... etc.

    Leaves http(s), data:, and # unchanged.
    """
    deriv_root = derivatives_root_from_report(report_path).resolve()
    deriv_name = deriv_root.name
    # Absolute path of the derivatives root with trailing slash — matches at any depth
    deriv_abs = str(deriv_root).replace("\\", "/").rstrip("/") + "/"

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

        # A) Absolute path of the actual derivatives root (slb_works at any nesting depth).
        #    Because bind mounts make container paths == host paths, this handles both
        #    flat layout (.../fitlins_derivatives/<name>/) and namespaced layout
        #    (.../fitlins_derivatives/tmth/<name>/) without hardcoding.
        out = strip_to_reports(deriv_abs)
        if out is not None:
            return out

        # B) Container-relative with slb_work prefix (legacy fallback): ../slb_work/... or ./slb_work/...
        for pref in ("../slb_work/", "./slb_work/"):
            if val.startswith(pref):
                # Namespaced: ../slb_work/fitlins_derivatives/<task_group>/<deriv_name>/...
                # Find the task_group component (parent of deriv_name) from deriv_abs
                _task_group_prefix = pref + "fitlins_derivatives/" + deriv_root.parent.name + "/" + deriv_name + "/"
                out = strip_to_reports(_task_group_prefix)
                if out is not None:
                    return out
                # Flat: ../slb_work/fitlins_derivatives/<deriv_name>/...
                out = strip_to_reports(pref + "fitlins_derivatives/" + deriv_name + "/")
                if out is not None:
                    return out
                # Oldest: ../slb_work/<deriv_name>/...
                out = strip_to_reports(pref + deriv_name + "/")
                if out is not None:
                    return out
                return val

        # C) Container absolute /slb_work/... (legacy fallback)
        if val.startswith("/slb_work/"):
            # Namespaced: /slb_work/fitlins_derivatives/<task_group>/<deriv_name>/...
            out = strip_to_reports("/slb_work/fitlins_derivatives/" + deriv_root.parent.name + "/" + deriv_name + "/")
            if out is not None:
                return out
            # Flat: /slb_work/fitlins_derivatives/<deriv_name>/...
            out = strip_to_reports("/slb_work/fitlins_derivatives/" + deriv_name + "/")
            if out is not None:
                return out
            # Oldest: /slb_work/<deriv_name>/...
            out = strip_to_reports("/slb_work/" + deriv_name + "/")
            if out is not None:
                return out
            return val

        # D) Root-absolute within derivatives tree: /node-runLevel/... -> ../node-runLevel/...
        if val.startswith("/"):
            return "../" + val.lstrip("/")

        return val

    def repl(m):
        attr, val = m.group(1), m.group(2)
        new_val = normalize(val)
        return m.group(0) if new_val == val else '{}="{}"'.format(attr, new_val)

    return re.sub(r'\b(src|href)="([^"]+)"', repl, html, flags=re.IGNORECASE)


def _task_glob_patterns(subj, task):
    """
    Build robust glob patterns for ortho PNGs.

    - If task is "tm" or "th" (as in FitLins report sections), match task-tm* (tm1, tm2, tm_run-1, etc.).
    - If task is already specific (e.g., tm1), still allow exact + prefix forms.
    - Also include patterns without task-* for subjectLevel figures.
    """
    base = "sub-{}".format(subj)
    pats = []

    if task:
        pats.append("{}_task-{}_{}_ortho.png".format(base, task, "*"))
        pats.append("{}_task-{}*_ortho.png".format(base, task))

    # subjectLevel figures may not have task in the name
    pats.append("{}_*_ortho.png".format(base))

    return pats


def find_ortho_pngs(deriv_root, subj, task, limit=8):
    out = []
    subj_dir = "sub-{}".format(subj)

    patterns = _task_glob_patterns(subj=subj, task=task)

    for node_dir in deriv_root.glob("node-*"):
        figdir = node_dir / "reports" / subj_dir / "figures"
        if not figdir.exists():
            continue
        for pat in patterns:
            out.extend(figdir.glob(pat))

    # De-duplicate, prefer stat-t first, then stable sort
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
            block = [
                "\n        <div class=\"reportlet\">\n",
                "          <p>Contrast figures found on disk (embedded via post-hoc patch):</p>\n",
            ]
            for fp in figs:
                rel = rel_from_reports(report_path, fp)
                block += [
                    "          <div style=\"margin: 8px 0;\">\n",
                    "            <div style=\"font-family: monospace; font-size: 12px;\">{}</div>\n".format(fp.name),
                    "            <img src=\"{}\" style=\"max-width: 100%; height: auto;\" />\n".format(rel),
                    "          </div>\n",
                ]
            block.append("        </div>\n")
            pieces.append("".join(block))
            n += 1
        elif verbose:
            sys.stderr.write("No ortho PNGs found for sub-{} task-{}\n".format(subj, task))

        last = m.end()

    pieces.append(html[last:])
    return "".join(pieces), n


def embed_local_images(html, report_path, verbose=False):
    """
    Replace src="<relative-path>" on all <img> tags with base64 data URIs,
    provided the file exists on disk relative to the reports/ directory.

    This makes the report fully self-contained regardless of which path
    format FitLins used (design matrices, correlation matrices, etc.).
    Contrast images already embedded as relative paths by inject_missing_contrast_blocks
    are also captured here, but data: URIs are left untouched.
    """
    reports_dir = report_path.parent.resolve()
    n = 0

    def repl(m):
        nonlocal n
        src = m.group(1)

        # Already a data URI or external link — leave untouched
        if src.startswith(("data:", "http://", "https://", "#")):
            return m.group(0)

        # Resolve relative to reports/
        candidate = (reports_dir / src).resolve()
        if not candidate.exists():
            # FitLins sometimes writes paths like ../data/sld/homes/.../node-X/...
            # which when resolved from reports/ produce a nonsense path.
            # If the src starts with one or more ../ segments followed by what
            # looks like an absolute path, strip the leading ../ components and
            # try the remainder as an absolute path.
            stripped = src
            while stripped.startswith("../"):
                stripped = stripped[3:]
            if stripped and not stripped.startswith("../"):
                abs_candidate = Path("/" + stripped)
                if abs_candidate.exists():
                    candidate = abs_candidate
                elif verbose:
                    sys.stderr.write("embed_local_images: not found (tried relative and absolute): {}\n".format(src))
                    return m.group(0)
            if not candidate.exists():
                if verbose:
                    sys.stderr.write("embed_local_images: not found: {}\n".format(candidate))
                return m.group(0)

        mime, _ = mimetypes.guess_type(str(candidate))
        if mime is None:
            # Fall back for SVG which mimetypes sometimes misses
            if candidate.suffix.lower() == ".svg":
                mime = "image/svg+xml"
            else:
                if verbose:
                    sys.stderr.write("embed_local_images: unknown mime for: {}\n".format(candidate))
                return m.group(0)

        data = base64.b64encode(candidate.read_bytes()).decode("ascii")
        n += 1
        return 'src="data:{};base64,{}"'.format(mime, data)

    html = re.sub(r'src="([^"]+)"', repl, html, flags=re.IGNORECASE)
    return html, n


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("Usage: fix_fitlins_reports.py <report.html> [--verbose]\n")
        return 2

    report = Path(argv[1])
    verbose = "--verbose" in argv

    if not report.exists():
        sys.stderr.write("ERROR: report not found: {}\n".format(report))
        return 1

    bak = report.with_suffix(report.suffix + ".bak")
    if not bak.exists():
        bak.write_text(report.read_text(errors="ignore"), errors="ignore")

    html = report.read_text(errors="ignore")
    html = rewrite_src_href_paths(html, report)
    html, n_patched = inject_missing_contrast_blocks(html, report, verbose)
    html, n_embedded = embed_local_images(html, report, verbose)
    report.write_text(html)

    print("Patched in place:", report)
    print("Backup:", bak)
    print("Patched missing-contrast blocks:", n_patched)
    print("Embedded local images as base64:", n_embedded)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))