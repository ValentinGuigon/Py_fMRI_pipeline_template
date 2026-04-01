#!/usr/bin/env python3
"""
generate_model_report.py — PDF report for one or more FitLins models.

Reads (all under --work-root):
  fitlins_models/{task_group}/{model}_smdl.json
  fitlins_derivatives/{task_group}/{model}_s{kernel}/reports/model-{Name}.html
  figures/{task_group}/{model}_s{kernel}/{node}_{thr_suffix}/manifest.tsv

PDF page order:
  1. Cover page           — index of all models + key parameters
  Per model:
  2. Model card           — GLM spec, contrasts with weights, config & plotting params
  3. Dataset-level head   — glass + slices per contrast  (from manifest)
  4. Subject-level page   — glass + slices per (subject, contrast), if node present
  5. Design matrix pages  — all subjects × (task, run) grid  (from HTML report)
  6. Correlation matrices — same layout
  7. Run-level results    — (task, run) blocks, subjects stacked;
                            lateralization models group by subject instead

Dependencies:
  pip install matplotlib numpy pandas pillow beautifulsoup4 cairosvg

Usage:
  python3 generate_model_report.py \
    --task-group tmth \
    --models tmth_visual_vs_baseline tmth_motor_lateralization \
    --work-root /data/sld/homes/vguigon/slb_work \
    --kernel 4 \
    --thr-suffix p-unc_p0.01_2s \
    --output /data/sld/homes/vguigon/slb_work/reports/tmth/tmth_report.pdf
"""

import argparse
import base64
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
import pandas as pd
from bs4 import BeautifulSoup
from PIL import Image

try:
    import cairosvg
    _HAS_CAIROSVG = True
except ImportError:
    _HAS_CAIROSVG = False
    print(
        "WARNING: cairosvg not found — will try external SVG converters for design/correlation matrices.\n"
        "         Recommended: pip install cairosvg",
        file=sys.stderr,
    )


# ── Page geometry (A4 portrait, inches) ──────────────────────────────────────
PAGE_W, PAGE_H = 8.27, 11.69
MARGIN = 0.35
CW = PAGE_W - 2 * MARGIN   # content width
CH = PAGE_H - 2 * MARGIN   # content height

TITLE_H    = 0.40   # page-level heading row height
ROW_H_DM   = 1.30   # inches per (task, run) row in design-matrix grid
ROW_H_RUN  = 1.55   # inches per subject row in run-level grid
HALF_H     = (CH - TITLE_H - 0.25) / 2   # height budget per two-up slot
SVG_SCALE  = 4.0


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ModelMeta:
    json_name:          str    # camelCase "Name" from JSON, e.g. "olVisualVsBaseline"
    stem:               str    # snake_case file stem, e.g. "ol_visual_vs_baseline"
    task_group:         str
    tasks:              list   # BIDS task labels, e.g. ["tm", "th"]
    subjects:           list   # subject IDs, e.g. ["000", "001", "002", "003"]
    contrasts:          list   # run-level explicit contrast names
    contrast_details:   list   # list of dicts: Name, ConditionList, Weights, Test
    run_node:           str    # Name of Run-level node, e.g. "runLevel"
    group_nodes:        list   # Non-run node names in hierarchy order
    is_lateralization:  bool   # contrasts include a left>right + right>left pair
    hrf_model:          str    # e.g. "spm"
    convolve_inputs:    list   # regressors convolved with HRF (trial_type.* names)
    nuisance_regressors: list  # non-task regressors in X (motion, compcor, etc.)
    node_hierarchy:     list   # Level names in order, e.g. ["Run","Subject","Dataset"]


@dataclass
class ConfigParams:
    """Key parameters read from the .cfg file for display on the model card."""
    smooth:         str = ""
    events_dir:     str = ""
    deriv_subdir:   str = ""
    deriv_label:    str = ""
    thr_mode:       str = ""
    p_unc:          str = ""
    alpha:          str = ""
    two_sided:      str = ""
    cluster_extent: str = ""
    display_mode:   str = ""
    plot_abs:       str = ""
    ncpus:          str = ""
    mem_gb:         str = ""


@dataclass
class RunEntry:
    """One run-level block extracted from the FitLins HTML report."""
    subject:    str
    task:       str
    run:        str                         # zero-padded, e.g. "01"
    design_img: Optional[Image.Image] = None
    corr_img:   Optional[Image.Image] = None


# ── Model JSON parsing ────────────────────────────────────────────────────────

def load_model_meta(json_path: Path, task_group: str, stem: str) -> ModelMeta:
    data = json.loads(json_path.read_text())

    tasks    = data["Input"].get("task",    [])
    subjects = data["Input"].get("subject", [])

    contrasts        = []
    contrast_details = []
    run_node         = "runLevel"
    group_nodes      = []
    node_hierarchy   = []
    hrf_model        = ""
    convolve_inputs  = []
    nuisance_regs    = []

    for node in data.get("Nodes", []):
        level = node.get("Level", "")
        name  = node.get("Name",  "")
        node_hierarchy.append(level)

        if level == "Run":
            run_node = name

            # HRF model and convolved regressors from Transformations
            xforms = node.get("Transformations", {}).get("Instructions", [])
            for xf in xforms:
                if xf.get("Name") == "Convolve":
                    hrf_model       = xf.get("Model", "")
                    convolve_inputs = xf.get("Input", [])

            # X matrix: separate task regressors from nuisance
            x_vars = node.get("Model", {}).get("X", [])
            nuisance_regs = [
                v for v in x_vars
                if isinstance(v, str) and not v.startswith("trial_type.")
            ]

            # Explicit contrasts with full details
            for c in node.get("Contrasts", []):
                contrasts.append(c["Name"])
                contrast_details.append({
                    "name":       c.get("Name", ""),
                    "conditions": c.get("ConditionList", []),
                    "weights":    c.get("Weights", []),
                    "test":       c.get("Test", "t"),
                })
        else:
            group_nodes.append(name)

    lower  = [c.lower() for c in contrasts]
    is_lat = any("left" in c for c in lower) and any("right" in c for c in lower)

    return ModelMeta(
        json_name=data.get("Name", stem),
        stem=stem,
        task_group=task_group,
        tasks=tasks,
        subjects=subjects,
        contrasts=contrasts,
        contrast_details=contrast_details,
        run_node=run_node,
        group_nodes=group_nodes,
        is_lateralization=is_lat,
        hrf_model=hrf_model,
        convolve_inputs=convolve_inputs,
        nuisance_regressors=nuisance_regs,
        node_hierarchy=node_hierarchy,
    )


# ── Config file parsing ───────────────────────────────────────────────────────

# Matches: KEY="value"  or  KEY=value  (ignores commented lines)
_CFG_VAR_RE = re.compile(
    r"""^[ \t]*([A-Z_]+)=["']?([^"'#\n]*)["']?[ \t]*(?:#.*)?$""",
    re.MULTILINE,
)


def load_config_file(cfg_path: Path) -> ConfigParams:
    """Parse a bash-style .cfg file and return a ConfigParams with the key values."""
    if not cfg_path.exists():
        print(f"  WARNING: config file not found: {cfg_path}", file=sys.stderr)
        return ConfigParams()

    text = cfg_path.read_text(encoding="utf-8")
    vars_ = {m.group(1): m.group(2).strip() for m in _CFG_VAR_RE.finditer(text)}

    return ConfigParams(
        smooth=        vars_.get("SMOOTH",         ""),
        events_dir=    vars_.get("EVENTS_DIR",      ""),
        deriv_subdir=  vars_.get("DERIV_SUBDIR",   ""),
        deriv_label=   vars_.get("DERIV_LABEL",    ""),
        thr_mode=      vars_.get("THR_MODE",        ""),
        p_unc=         vars_.get("P_UNC",           ""),
        alpha=         vars_.get("ALPHA",           ""),
        two_sided=     vars_.get("TWO_SIDED",       ""),
        cluster_extent=vars_.get("CLUSTER_EXTENT",  ""),
        display_mode=  vars_.get("DISPLAY_MODE",    ""),
        plot_abs=      vars_.get("PLOT_ABS",        ""),
        ncpus=         vars_.get("NCPUS",           ""),
        mem_gb=        vars_.get("MEM_GB",          ""),
    )


# ── HTML report parsing ───────────────────────────────────────────────────────

def _decode_svg_b64(b64_str: str) -> Optional[Image.Image]:
    """Decode a base64-encoded SVG → PIL Image."""
    if not _HAS_CAIROSVG:
        return None
    try:
        svg_bytes = base64.b64decode(b64_str)
        png_bytes = cairosvg.svg2png(bytestring=svg_bytes, scale=SVG_SCALE)
        return Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    except Exception as exc:
        print(f"  WARNING: SVG decode failed: {exc}", file=sys.stderr)
        return None


# Matches: "Run: 1, Subject: 000, Task: obslearn"
_SUMMARY_RE = re.compile(
    r"Run:\s*(\d+),\s*Subject:\s*(\w+),\s*Task:\s*(\S+)",
    re.IGNORECASE,
)

_RUN_FIG_RE = re.compile(
    r"sub-(?P<subject>\w+)_task-(?P<task>[^_]+)_run-(?P<run>\d+)_(?P<kind>design|corr)\.svg$",
    re.IGNORECASE,
)


def parse_html_report(html_path: Path) -> list:
    """
    Extract (subject, task, run, design_img, corr_img) from the FitLins HTML report.

    Exploits this structure inside each <details> block:
      <summary class="heading-1">Run: N, Subject: XXX, Task: TASK</summary>
      [<div class="warning">...]
      <img src="data:image/svg+xml;base64,..."/>   ← design matrix
      <h4>Correlation matrix</h4>
      <p>...</p>
      <img src="data:image/svg+xml;base64,..."/>   ← correlation matrix
    """
    if not html_path.exists():
        print(f"  WARNING: HTML report not found: {html_path}", file=sys.stderr)
        return []

    soup    = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    entries = []

    for details in soup.find_all("details"):
        summary = details.find("summary", class_="heading-1")
        if not summary:
            continue
        m = _SUMMARY_RE.search(summary.get_text())
        if not m:
            continue

        run_num, subject, task = m.group(1), m.group(2), m.group(3)
        run_str = f"{int(run_num):02d}"

        report_dir = html_path.parent

        # Design matrix: first <img> directly inside this <details>
        design_tag = details.find("img")
        design_img = None
        if design_tag and "_design.svg" in design_tag.get("src", "").lower():
            design_img = _load_report_image(design_tag.get("src", ""), report_dir)

        # Correlation matrix: <img> that immediately follows <h4>Correlation matrix</h4>
        corr_h4  = details.find("h4", string=lambda t: t and "Correlation" in t)
        corr_img = None
        if corr_h4:
            corr_tag = corr_h4.find_next("img")
            if corr_tag and "_corr.svg" in corr_tag.get("src", "").lower():
                corr_img = _load_report_image(corr_tag.get("src", ""), report_dir)

        entries.append(RunEntry(
            subject=subject, task=task, run=run_str,
            design_img=design_img, corr_img=corr_img,
        ))

    return entries


def collect_run_entries_from_report_files(deriv_root: Path) -> list:
    """Scan FitLins run-report figure directories directly for design/corr SVGs."""
    run_reports_root = deriv_root / "node-runLevel" / "reports"
    if not run_reports_root.exists():
        return []

    merged = {}
    for svg_path in sorted(run_reports_root.rglob("*.svg")):
        m = _RUN_FIG_RE.search(svg_path.name)
        if not m:
            continue

        key = (
            m.group("subject"),
            m.group("task"),
            f"{int(m.group('run')):02d}",
        )
        entry = merged.setdefault(
            key,
            RunEntry(subject=key[0], task=key[1], run=key[2]),
        )

        img = _load_report_image(str(svg_path), svg_path.parent)
        if m.group("kind").lower() == "design":
            entry.design_img = img
        else:
            entry.corr_img = img

    return list(merged.values())


# ── Manifest & PNG loading ────────────────────────────────────────────────────

def load_manifest(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        print(f"  WARNING: manifest not found: {path}", file=sys.stderr)
        return None
    df = pd.read_csv(path, sep="\t", dtype=str).fillna("")
    return df


_WORK_ROOT_FOR_RESOLVE: Optional[Path] = None   # set by generate_report()
_TASK_GROUP_FOR_RESOLVE: str = ""
_WARNED_MESSAGES = set()


def _warn_once(message: str):
    if message in _WARNED_MESSAGES:
        return
    _WARNED_MESSAGES.add(message)
    print(message, file=sys.stderr)


def _resolve_existing_path(path_str: str, base_dir: Optional[Path] = None) -> Optional[Path]:
    """Resolve image/file paths across cluster and local work-root layouts."""
    if not path_str or path_str in ("nan", "None", ""):
        return None

    normalized = path_str.strip()
    if normalized.startswith("file://"):
        normalized = normalized[7:]

    raw_path = Path(normalized)
    candidates = []

    if base_dir is not None:
        candidates.append((base_dir / raw_path).resolve())
    candidates.append(raw_path)

    if _WORK_ROOT_FOR_RESOLVE:
        work = _WORK_ROOT_FOR_RESOLVE
        parts = [part for part in raw_path.parts if part not in (raw_path.anchor, "/", "\\")]

        if raw_path.is_absolute():
            try:
                candidates.append(work / raw_path.relative_to(raw_path.anchor))
            except ValueError:
                pass

        for anchor in ("fitlins_derivatives", "figures", "fitlins_models", "fitlins_configs", "reports"):
            if anchor not in parts:
                continue
            idx = parts.index(anchor)
            tail = parts[idx + 1:]
            if not tail:
                continue
            candidates.append(work / anchor / Path(*tail))
            if _TASK_GROUP_FOR_RESOLVE and tail[0] != _TASK_GROUP_FOR_RESOLVE:
                candidates.append(work / anchor / _TASK_GROUP_FOR_RESOLVE / Path(*tail))

    seen = set()
    for candidate in candidates:
        candidate_str = str(candidate)
        if candidate_str in seen:
            continue
        seen.add(candidate_str)
        if candidate.exists():
            return candidate

    return None


def _decode_data_uri(uri: str) -> Optional[Image.Image]:
    """Decode a base64 image data URI to a PIL image."""
    if not uri.startswith("data:") or "," not in uri:
        return None
    header, payload = uri.split(",", 1)
    if ";base64" not in header:
        return None

    try:
        raw = base64.b64decode(payload)
    except Exception as exc:
        print(f"  WARNING: data URI decode failed: {exc}", file=sys.stderr)
        return None

    if "image/svg+xml" in header:
        if not _HAS_CAIROSVG:
            return None
        try:
            png_bytes = cairosvg.svg2png(bytestring=raw, scale=SVG_SCALE)
            return Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        except Exception as exc:
            print(f"  WARNING: SVG data URI decode failed: {exc}", file=sys.stderr)
            return None

    try:
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception as exc:
        print(f"  WARNING: raster data URI decode failed: {exc}", file=sys.stderr)
        return None


def _svg_file_to_image(svg_path: Path) -> Optional[Image.Image]:
    """Render an SVG file to a PIL image using cairosvg or common CLI tools."""
    if _HAS_CAIROSVG:
        try:
            png_bytes = cairosvg.svg2png(url=str(svg_path), scale=SVG_SCALE)
            return Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        except Exception as exc:
            print(f"  WARNING: cairosvg failed for '{svg_path}': {exc}", file=sys.stderr)

    converters = []
    for tool in ("rsvg-convert", "inkscape", "magick", "convert"):
        exe = shutil.which(tool)
        if exe:
            converters.append((tool, exe))

    for tool, exe in converters:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            if tool == "rsvg-convert":
                cmd = [exe, "-o", str(tmp_path), str(svg_path)]
            elif tool == "inkscape":
                cmd = [exe, str(svg_path), "--export-type=png", f"--export-filename={tmp_path}"]
            elif tool == "magick":
                cmd = [exe, str(svg_path), str(tmp_path)]
            else:
                cmd = [exe, str(svg_path), str(tmp_path)]

            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if proc.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0:
                return Image.open(tmp_path).convert("RGBA")
        except Exception:
            pass
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    _warn_once(
        "  WARNING: no usable SVG renderer found; install cairosvg or make one of "
        "rsvg-convert/inkscape/magick available on PATH."
    )
    return None


def _load_png(path_str: str) -> Optional[np.ndarray]:
    """Load a PNG file path → RGBA numpy array; return None on failure.

    Tries the path as-is first (absolute or CWD-relative).  If that fails
    and the path looks absolute, also tries resolving it relative to
    _WORK_ROOT_FOR_RESOLVE so that manifests generated on a server with a
    different mount point still resolve locally.
    """
    if not path_str or path_str in ("nan", "None", ""):
        return None

    resolved = _resolve_existing_path(path_str)
    if resolved is None:
        return None

    try:
        return np.array(Image.open(resolved).convert("RGBA"))
    except Exception:
        return None


def _load_report_image(src: str, report_dir: Path) -> Optional[Image.Image]:
    """Load a design/correlation image from an HTML img src attribute."""
    if not src:
        return None
    if src.startswith("data:"):
        return _decode_data_uri(src)

    resolved = _resolve_existing_path(src, base_dir=report_dir)
    if resolved is None:
        return None

    try:
        if resolved.suffix.lower() == ".svg":
            return _svg_file_to_image(resolved)
        return Image.open(resolved).convert("RGBA")
    except Exception as exc:
        print(f"  WARNING: could not load report image '{src}': {exc}", file=sys.stderr)
    return None


# ── matplotlib helpers ────────────────────────────────────────────────────────

def _new_page() -> plt.Figure:
    fig = plt.figure(figsize=(PAGE_W, PAGE_H))
    fig.patch.set_facecolor("white")
    return fig


def _page_title(fig: plt.Figure, title: str, subtitle: str = "",
                y_top: float = 1.0):
    """Write a bold page title + optional grey subtitle at y_top (figure fraction)."""
    y = y_top - MARGIN / PAGE_H
    fig.text(0.5, y, title, ha="center", va="top",
             fontsize=12, fontweight="bold")
    if subtitle:
        fig.text(0.5, y - 0.030, subtitle, ha="center", va="top",
                 fontsize=8, color="#555555")


def _show_img(ax: plt.Axes, img, title: str = "", interpolation: str = "lanczos"):
    """Display a PIL Image or ndarray in ax with no frame; placeholder if None."""
    if img is None:
        ax.set_facecolor("#f2f2f2")
        ax.text(0.5, 0.5, "N/A", ha="center", va="center",
                fontsize=7, color="#bbbbbb", transform=ax.transAxes)
    else:
        if isinstance(img, Image.Image):
            img = np.array(img)
        ax.imshow(img, aspect="equal", interpolation=interpolation)
        ax.set_anchor("C")
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)
    if title:
        ax.set_title(title, fontsize=6.5, pad=2, color="#222222")


def _hline(fig: plt.Figure, y_frac: float):
    """Draw a light horizontal divider across the content width."""
    x0 = MARGIN / PAGE_W
    x1 = 1.0 - x0
    line = plt.Line2D([x0, x1], [y_frac, y_frac], transform=fig.transFigure,
                      color="#cccccc", linewidth=0.7, linestyle="--")
    fig.add_artist(line)


# ── Axes placement helper (all coordinates in figure fractions) ───────────────

def _ax(fig: plt.Figure, left: float, bottom: float,
        width: float, height: float) -> plt.Axes:
    """Add an Axes with coordinates given as figure fractions."""
    return fig.add_axes([left, bottom, width, height])


# ── Cover page ────────────────────────────────────────────────────────────────

def write_cover_page(pdf: PdfPages, task_group: str,
                     metas: list, thr_suffix: str, kernel: int):
    fig = _new_page()
    cx  = 0.5
    cy  = 0.72

    fig.text(cx, cy,        "fMRI Analysis Report",
             ha="center", va="center", fontsize=20, fontweight="bold")
    fig.text(cx, cy - 0.06, f"Task group: {task_group.upper()}",
             ha="center", va="center", fontsize=13, color="#222222")
    fig.text(cx, cy - 0.11, f"Smoothing kernel: {kernel} mm FWHM",
             ha="center", va="center", fontsize=10, color="#444444")
    fig.text(cx, cy - 0.15, f"Threshold: {thr_suffix}",
             ha="center", va="center", fontsize=10, color="#444444")
    fig.text(cx, cy - 0.19, f"Generated: {date.today().isoformat()}",
             ha="center", va="center", fontsize=9,  color="#666666")

    # Model listing
    y = cy - 0.28
    fig.text(cx, y, "Models included",
             ha="center", fontsize=11, fontweight="bold")
    y -= 0.03
    for meta in metas:
        tasks_s = ", ".join(meta.tasks)
        subj_s  = ", ".join(meta.subjects)
        cont_s  = ", ".join(meta.contrasts)
        lat_tag = "  [lateralization]" if meta.is_lateralization else ""
        fig.text(0.12, y, f"{meta.stem}{lat_tag}",
                 fontsize=9, fontweight="bold", va="top", color="#111111")
        fig.text(0.12, y - 0.022,
                 f"  task: {tasks_s}  |  subjects: {subj_s}  |  contrasts: {cont_s}",
                 fontsize=7.5, va="top", color="#555555")
        y -= 0.058

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


# ── Model card page ──────────────────────────────────────────────────────────

def write_model_card_page(pdf: PdfPages, meta: ModelMeta, cfg: ConfigParams):
    """
    One-page methods summary for a model:
      • Header  — model name, tasks, subjects, node hierarchy
      • Left    — GLM specification (events modelled, HRF, nuisance regressors)
      • Right   — Contrast table (name | conditions & weights | test)
      • Footer  — Pipeline params | Plotting params
    """
    fig = _new_page()

    # ── helpers ──────────────────────────────────────────────────────────────
    lm = MARGIN / PAGE_W          # left margin fraction
    rm = 1.0 - MARGIN / PAGE_W    # right edge fraction
    cw = rm - lm                  # content width fraction

    def txt(x, y, s, **kw):
        fig.text(x, y, s, **kw)

    def section(x, y, label):
        """Bold section label with a coloured underline rule."""
        txt(x, y, label, fontsize=8, fontweight="bold", color="#1a1a1a", va="top")
        line = plt.Line2D(
            [x, x + cw * 0.48], [y - 0.008, y - 0.008],
            transform=fig.transFigure, color="#4472C4", linewidth=1.0,
        )
        fig.add_artist(line)
        return y - 0.028   # y after label

    def bullet(x, y, text, indent=0.010, fs=7.5, color="#222222"):
        txt(x + indent, y, text, fontsize=fs, color=color, va="top")
        return y - 0.022

    def wrap_bullet(x, y, text, max_chars=72, indent=0.010, fs=7.5):
        """Wrap long text across lines."""
        lines = textwrap.wrap(text, max_chars)
        for line in lines:
            y = bullet(x, y, line, indent=indent, fs=fs)
        return y

    # ── Header band ──────────────────────────────────────────────────────────
    y = 1.0 - MARGIN / PAGE_H - 0.01
    txt(lm, y, meta.stem, fontsize=13, fontweight="bold", color="#1a1a1a", va="top")
    y -= 0.030
    hierarchy_str = "  →  ".join(meta.node_hierarchy)
    txt(lm, y,
        f"tasks: {', '.join(meta.tasks)}    "
        f"subjects ({len(meta.subjects)}): {', '.join(meta.subjects)}    "
        f"nodes: {hierarchy_str}",
        fontsize=7.5, color="#444444", va="top")
    y -= 0.012
    # thin horizontal rule under header
    fig.add_artist(plt.Line2D(
        [lm, rm], [y, y], transform=fig.transFigure,
        color="#888888", linewidth=0.6,
    ))
    y -= 0.022

    body_top = y   # start of two-column body

    # ── LEFT column: GLM specification ───────────────────────────────────────
    col_l  = lm
    col_r  = lm + cw * 0.52    # right column starts here
    col_sep = lm + cw * 0.505

    # vertical separator
    sep_top    = body_top + 0.005
    sep_bottom = body_top - 0.52   # will be trimmed visually

    y_l = body_top

    y_l = section(col_l, y_l, "GLM specification")

    # Events modelled (convolved with HRF)
    txt(col_l + 0.010, y_l, f"HRF model: {meta.hrf_model or '—'}",
        fontsize=7.5, color="#333333", va="top")
    y_l -= 0.022
    txt(col_l + 0.010, y_l, "Convolved regressors:",
        fontsize=7.5, color="#333333", va="top", fontstyle="italic")
    y_l -= 0.020
    for reg in meta.convolve_inputs:
        short = reg.replace("trial_type.", "")
        y_l = bullet(col_l, y_l, f"• {short}", indent=0.020)

    y_l -= 0.010
    txt(col_l + 0.010, y_l, "Nuisance regressors:",
        fontsize=7.5, color="#333333", va="top", fontstyle="italic")
    y_l -= 0.020

    # Group nuisance regressors for compactness
    motion   = [r for r in meta.nuisance_regressors
                if any(k in r for k in ("trans_", "rot_", "framewise"))]
    compcor  = [r for r in meta.nuisance_regressors
                if "comp_cor" in r]
    other    = [r for r in meta.nuisance_regressors
                if r not in motion and r not in compcor]

    if motion:
        y_l = bullet(col_l, y_l,
                     f"• Motion ({len(motion)}): {', '.join(motion)}", indent=0.020)
    if compcor:
        y_l = bullet(col_l, y_l,
                     f"• CompCor ({len(compcor)}): "
                     + ", ".join(compcor[:3])
                     + ("…" if len(compcor) > 3 else ""),
                     indent=0.020)
    for r in other:
        y_l = bullet(col_l, y_l, f"• {r}", indent=0.020)

    if 1 in [v for node_data in [] for v in []]:   # placeholder — intercept note
        pass
    y_l -= 0.006
    txt(col_l + 0.010, y_l, "Intercept (1) included in X",
        fontsize=6.5, color="#666666", va="top", fontstyle="italic")
    y_l -= 0.022

    # ── RIGHT column: Contrast table ─────────────────────────────────────────
    y_r = body_top
    y_r = section(col_r, y_r, "Contrasts")

    for cd in meta.contrast_details:
        # Contrast name + test type
        txt(col_r + 0.010, y_r,
            f"{cd['name']}  [{cd['test']}-test]",
            fontsize=7.5, fontweight="bold", color="#1a1a1a", va="top")
        y_r -= 0.020

        # Conditions and weights on one line each (truncate if very long)
        conds   = cd.get("conditions", [])
        weights = cd.get("weights", [])
        pairs   = list(zip(conds, weights))

        for cond, w in pairs:
            short = cond.replace("trial_type.", "")
            sign  = f"{w:+.2g}" if isinstance(w, (int, float)) else str(w)
            y_r = bullet(col_r, y_r, f"  {sign:>6}  ×  {short}",
                         indent=0.014, fs=7.0, color="#333333")
        y_r -= 0.010   # gap between contrasts

    # ── Vertical separator between columns ───────────────────────────────────
    fig.add_artist(plt.Line2D(
        [col_sep, col_sep],
        [min(y_l, y_r) - 0.01, sep_top],
        transform=fig.transFigure, color="#cccccc", linewidth=0.5,
    ))

    # ── Footer: pipeline + plotting parameters ────────────────────────────────
    footer_y = min(y_l, y_r) - 0.030
    fig.add_artist(plt.Line2D(
        [lm, rm], [footer_y + 0.005, footer_y + 0.005],
        transform=fig.transFigure, color="#888888", linewidth=0.6,
    ))

    # Pipeline params (left half of footer)
    fp_x = lm
    y_f  = footer_y - 0.008

    y_f = section(fp_x, y_f, "Pipeline parameters")

    def _row(x, y, label, value):
        txt(x + 0.010, y, f"{label}:", fontsize=7, color="#555555", va="top",
            fontstyle="italic")
        txt(x + 0.120, y, value or "—",  fontsize=7, color="#111111", va="top")
        return y - 0.020

    y_f = _row(fp_x, y_f, "Smoothing",    cfg.smooth)
    y_f = _row(fp_x, y_f, "Events dir",
               (cfg.events_dir.split("/")[-1] if cfg.events_dir else ""))
    y_f = _row(fp_x, y_f, "Deriv subdir", cfg.deriv_subdir)
    y_f = _row(fp_x, y_f, "Deriv label",  cfg.deriv_label)
    y_f = _row(fp_x, y_f, "CPUs / RAM",
               f"{cfg.ncpus} / {cfg.mem_gb} GB" if cfg.ncpus else "")

    # Plotting params (right half of footer)
    pp_x = lm + cw * 0.52
    y_p  = footer_y - 0.008

    y_p = section(pp_x, y_p, "Plotting parameters")

    # Threshold description
    thr_map = {"p-unc": "Uncorr. p-value", "fdr": "FDR", "bonferroni": "Bonferroni",
               "ari": "ARI", "fixed": "Fixed value", "none": "None"}
    thr_label = thr_map.get(cfg.thr_mode, cfg.thr_mode)
    thr_value = cfg.p_unc if cfg.thr_mode == "p-unc" else cfg.alpha
    thr_str   = f"{thr_label}  (p < {thr_value})" if thr_value else thr_label

    two_s  = "two-sided" if cfg.two_sided == "1" else "one-sided"
    abs_s  = "absolute values" if cfg.plot_abs == "1" else "signed"

    y_p = _row(pp_x, y_p, "Threshold",      thr_str)
    y_p = _row(pp_x, y_p, "Sidedness",      two_s)
    y_p = _row(pp_x, y_p, "Cluster extent", f"≥ {cfg.cluster_extent} voxels"
               if cfg.cluster_extent else "—")
    y_p = _row(pp_x, y_p, "Display mode",   cfg.display_mode)
    y_p = _row(pp_x, y_p, "Colormap sign",  abs_s)

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


# ── Group-level pages (dataset, subject) ─────────────────────────────────────

def write_group_level_pages(pdf: PdfPages, meta: ModelMeta, manifests: dict):
    """
    For each group-level node (subject, dataset):
    - Dataset-level (sub column empty): one row per contrast → [glass | slices]
    - Subject-level (sub column filled): one row per (subject) per contrast block
    """
    for node_name in meta.group_nodes:
        df = manifests.get(node_name)
        if df is None or df.empty:
            continue

        has_sub = "sub" in df.columns and df["sub"].str.strip().ne("").any()
        contrasts = sorted(df["contrast"].unique()) if "contrast" in df.columns else []
        if not contrasts:
            continue

        if has_sub:
            _write_subject_level_page(pdf, meta, df, node_name, contrasts)
        else:
            _write_dataset_level_page(pdf, meta, df, node_name, contrasts)


def _write_dataset_level_page(pdf, meta, df, node_name, contrasts):
    """Dataset-level: one row per contrast, columns = [label | glass]."""
    n = len(contrasts)
    fig = _new_page()
    subtitle = f"task: {', '.join(meta.tasks)}  |  {n} contrast(s)"
    _page_title(fig, f"{meta.stem}  —  {node_name} results", subtitle)

    title_frac = (TITLE_H + 0.05) / PAGE_H
    row_frac   = (CH - TITLE_H - 0.05) / PAGE_H / max(n, 1)

    label_w  = 0.12
    glass_w  = 0.58
    x_label  = MARGIN / PAGE_W
    x_glass  = x_label + label_w

    y_top = 1.0 - MARGIN / PAGE_H - title_frac

    for i, contrast in enumerate(contrasts):
        row = df[df["contrast"] == contrast]
        row = row.iloc[0] if len(row) else None

        y_bot = y_top - (i + 1) * row_frac
        y_mid = y_top - i * row_frac - row_frac / 2

        fig.text(x_label, y_mid, contrast, ha="left", va="center",
                 fontsize=7.5, fontweight="bold", color="#222222")

        pad = 0.006
        ax_g = _ax(fig, x_glass,  y_bot + pad, glass_w,  row_frac - 2 * pad)
        _show_img(ax_g, _load_png(row["glass_png"]  if row is not None else ""), interpolation="none")

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _write_subject_level_page(pdf, meta, df, node_name, contrasts):
    """
    Subject-level: for each contrast block, rows = subjects → [glass].
    Two-up: two contrast blocks per page when they fit.
    """
    subjects = sorted(df["sub"].unique())
    n_subj   = len(subjects)

    # Estimate block height: title row + n_subj rows
    block_h_in = 0.30 + n_subj * ROW_H_RUN * 0.85
    can_two_up = block_h_in <= HALF_H

    label_w  = 0.09
    glass_w  = 0.58
    x_label  = MARGIN / PAGE_W
    x_glass  = x_label + label_w

    def _draw_block(fig, contrast, y_top_f, block_h_f):
        sub_df = df[df["contrast"] == contrast]
        row_h_f = (block_h_f - 0.04) / max(n_subj, 1)
        for j, subj in enumerate(subjects):
            row = sub_df[sub_df["sub"] == subj]
            row = row.iloc[0] if len(row) else None
            y_bot = y_top_f - (j + 1) * row_h_f
            y_mid = y_top_f - j * row_h_f - row_h_f / 2
            fig.text(x_label, y_mid, f"sub-{subj}", ha="left", va="center",
                     fontsize=6.5, color="#333333")
            pad = 0.004
            ax_g = _ax(fig, x_glass,  y_bot + pad, glass_w,  row_h_f - 2 * pad)
            _show_img(ax_g, _load_png(row["glass_png"]  if row is not None else ""), interpolation="none")

    i = 0
    block_h_f = block_h_in / PAGE_H
    title_frac = (TITLE_H + 0.05) / PAGE_H

    while i < len(contrasts):
        fig = _new_page()
        _page_title(fig, f"{meta.stem}  —  {node_name} results")
        y_top = 1.0 - MARGIN / PAGE_H - title_frac

        c1 = contrasts[i]
        fig.text(MARGIN / PAGE_W, y_top, c1, ha="left", va="top",
                 fontsize=9, fontweight="bold", color="#1a1a1a")
        _draw_block(fig, c1, y_top - 0.018, block_h_f)

        if can_two_up and i + 1 < len(contrasts):
            y_div  = y_top - block_h_f - 0.01
            _hline(fig, y_div)
            y_top2 = y_div - 0.018
            c2 = contrasts[i + 1]
            fig.text(MARGIN / PAGE_W, y_top2, c2, ha="left", va="top",
                     fontsize=9, fontweight="bold", color="#1a1a1a")
            _draw_block(fig, c2, y_top2 - 0.018, block_h_f)
            i += 2
        else:
            i += 1

        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


# ── Design / Correlation matrix pages ────────────────────────────────────────

def write_matrix_pages(pdf: PdfPages, run_entries: list,
                       meta: ModelMeta, which: str):
    """
    Grid layout: columns = subjects, rows = (task, run) combinations.
    Packs as many rows as fit on one page (ROW_H_DM per row).

    which: "design" | "corr"
    """
    if not run_entries:
        return

    subjects  = sorted({e.subject for e in run_entries})
    task_runs = sorted({(e.task, e.run) for e in run_entries})
    n_subj    = len(subjects)
    label     = "Design matrices" if which == "design" else "Correlation matrices"

    lookup = {
        (e.task, e.run, e.subject):
            (e.design_img if which == "design" else e.corr_img)
        for e in run_entries
    }

    title_frac   = (TITLE_H + 0.05) / PAGE_H
    header_frac  = 0.028            # column-label row
    avail_h      = CH - TITLE_H - 0.05 - header_frac * PAGE_H   # content inches
    rows_per_page = max(1, min(4, int(avail_h // ROW_H_DM)))

    row_label_w = 0.55             # inches reserved for "(task, run)" label
    col_w_in    = (CW - row_label_w) / n_subj
    col_w_f     = col_w_in / PAGE_W
    x_label_f   = MARGIN / PAGE_W
    x_col0_f    = x_label_f + row_label_w / PAGE_W

    for page_idx in range(0, len(task_runs), rows_per_page):
        page_trs = task_runs[page_idx:page_idx + rows_per_page]
        n_rows   = len(page_trs)
        row_h_f  = (avail_h / PAGE_H) / max(n_rows, 1)   # actual row frac this page

        fig = _new_page()
        page_num = page_idx // rows_per_page + 1
        _page_title(fig, f"{meta.stem}  —  {label}",
                    f"page {page_num}" if len(task_runs) > rows_per_page else "")

        y_header = 1.0 - MARGIN / PAGE_H - title_frac

        # Column headers (subject IDs)
        for j, subj in enumerate(subjects):
            fig.text(x_col0_f + j * col_w_f + col_w_f / 2,
                     y_header, f"sub-{subj}",
                     ha="center", va="bottom", fontsize=7, color="#333333")

        y_grid_top = y_header - header_frac

        for i, (task, run) in enumerate(page_trs):
            y_bot = y_grid_top - (i + 1) * row_h_f
            y_mid = y_grid_top - i * row_h_f - row_h_f / 2

            # Row label
            fig.text(x_label_f, y_mid,
                     f"task-{task}\nrun-{run}",
                     ha="left", va="center", fontsize=6, color="#444444",
                     linespacing=1.3)

            # Subject image cells
            for j, subj in enumerate(subjects):
                img = lookup.get((task, run, subj))
                pad = 0.004
                ax  = _ax(fig,
                          x_col0_f + j * col_w_f + pad,
                          y_bot + pad,
                          col_w_f - 2 * pad,
                          row_h_f - 2 * pad)
                _show_img(ax, img, interpolation="none")

        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


# ── Run-level result pages ────────────────────────────────────────────────────

def _block_height_in(n_rows: int) -> float:
    """Estimate block height in inches: title + n_rows subject rows."""
    return 0.30 + n_rows * ROW_H_RUN


def _draw_run_block(fig: plt.Figure, block_df: pd.DataFrame,
                    subjects_to_show: list, contrasts: list,
                    y_top_f: float, block_h_f: float):
    """
    Draw one (task, run[, subject]) result block.

    Layout per subject row: [glass_c0 | glass_c1 | ...]
    """
    n_contrasts = len(contrasts)
    n_rows      = len(subjects_to_show)
    row_h_f     = (block_h_f - 0.04) / max(n_rows, 1)

    label_w_f  = 0.08 / PAGE_W * CW / CW   # relative to page: ~0.08 inch
    x_label_f  = MARGIN / PAGE_W
    x_data_f   = x_label_f + label_w_f * 1.1
    data_w_f   = (1.0 - MARGIN / PAGE_W) - x_data_f - 0.008

    gap_f  = 0.008
    unit_f = (data_w_f - max(0, n_contrasts - 1) * gap_f) / max(n_contrasts, 1)

    for i, subj in enumerate(subjects_to_show):
        y_bot = y_top_f - (i + 1) * row_h_f
        y_mid = y_top_f - i * row_h_f - row_h_f / 2

        fig.text(x_label_f, y_mid, f"sub-{subj}",
                 ha="left", va="center", fontsize=6.5, color="#333333")

        for c_idx, contrast in enumerate(contrasts):
            rows = block_df[
                (block_df["sub"] == subj) &
                (block_df["contrast"] == contrast)
            ]
            row = rows.iloc[0] if len(rows) else None

            x_glass  = x_data_f + c_idx * (unit_f + gap_f)

            pad = 0.004
            ax_g = _ax(fig, x_glass + pad,  y_bot + pad,
                        unit_f - 2 * pad, row_h_f - 2 * pad)

            # Contrast label only in first subject row
            ctitle = contrast if i == 0 else ""
            _show_img(ax_g, _load_png(row["glass_png"]  if row is not None else ""),
                      interpolation="none",
                      title=ctitle)


def write_run_level_pages(pdf: PdfPages, meta: ModelMeta, manifests: dict):
    """
    For each (task, run) — or (task, run, subject) for lateralization models —
    draw a block showing all subjects' glass + slices per contrast.
    Two-up: pack two blocks per physical page when their heights allow.
    """
    df = manifests.get(meta.run_node)
    if df is None or df.empty:
        return

    subjects  = sorted(df["sub"].unique())      if "sub"      in df.columns else meta.subjects
    contrasts = sorted(df["contrast"].unique()) if "contrast" in df.columns else meta.contrasts
    tasks     = sorted(df["task"].unique())     if "task"     in df.columns else meta.tasks
    runs      = sorted(df["run"].unique())      if "run"      in df.columns else []

    # Build ordered block list
    blocks = []
    for task in tasks:
        for run in runs:
            bdf = df[(df["task"] == task) & (df["run"] == run)]
            if bdf.empty:
                continue
            if meta.is_lateralization:
                # One block per subject, showing both L>R and R>L contrasts
                for subj in subjects:
                    sdf = bdf[bdf["sub"] == subj]
                    if sdf.empty:
                        continue
                    blocks.append({
                        "title":    f"task-{task}  |  run-{run}  |  sub-{subj}",
                        "subjects": [subj],
                        "df":       bdf,
                    })
            else:
                blocks.append({
                    "title":    f"task-{task}  |  run-{run}",
                    "subjects": subjects,
                    "df":       bdf,
                })

    if not blocks:
        return

    # Decide on two-up eligibility (all blocks have the same subject count)
    n_rows_per_block = 1 if meta.is_lateralization else len(subjects)
    block_h_in  = _block_height_in(n_rows_per_block)
    can_two_up  = block_h_in <= HALF_H
    block_h_f   = block_h_in / PAGE_H

    title_frac = (TITLE_H + 0.05) / PAGE_H
    page_title = f"{meta.stem}  —  run-level results"

    i = 0
    while i < len(blocks):
        fig = _new_page()
        _page_title(fig, page_title)
        y_top = 1.0 - MARGIN / PAGE_H - title_frac

        b1 = blocks[i]
        fig.text(MARGIN / PAGE_W, y_top, b1["title"],
                 ha="left", va="top", fontsize=9, fontweight="bold",
                 color="#1a1a1a")
        _draw_run_block(fig, b1["df"], b1["subjects"], contrasts,
                        y_top - 0.012, block_h_f)

        if can_two_up and i + 1 < len(blocks):
            y_div = y_top - block_h_f - 0.012
            _hline(fig, y_div)
            b2 = blocks[i + 1]
            y_top2 = y_div - 0.015
            fig.text(MARGIN / PAGE_W, y_top2, b2["title"],
                     ha="left", va="top", fontsize=9, fontweight="bold",
                     color="#1a1a1a")
            _draw_run_block(fig, b2["df"], b2["subjects"], contrasts,
                            y_top2 - 0.012, block_h_f)
            i += 2
        else:
            i += 1

        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


# ── Orchestrator ──────────────────────────────────────────────────────────────

def _thr_dir(node_name: str, thr_suffix: str) -> str:
    return f"{node_name}_{thr_suffix}"


def _find_manifest(fig_root: Path, node: str, thr_suffix: str) -> Optional[Path]:
    """
    Locate manifest.tsv for *node* under *fig_root*.

    Strategy (in order):
    1. Exact match:  fig_root/{node}_{thr_suffix}/manifest.tsv
    2. Glob fallback: fig_root/{node}_*/manifest.tsv  (picks first alphabetically)
       Used when the user's --thr-suffix doesn't exactly match the directory that
       run_pipeline.sh created (e.g. p0.01 vs p0.001, or missing _df/k suffixes).
    """
    exact = fig_root / _thr_dir(node, thr_suffix) / "manifest.tsv"
    if exact.exists():
        return exact

    if fig_root.exists():
        candidates = sorted(fig_root.glob(f"{node}_*/manifest.tsv"))
        if candidates:
            chosen = candidates[0]
            print(
                f"  INFO: thr-suffix '{thr_suffix}' not found; "
                f"auto-selected '{chosen.parent.name}'",
                file=sys.stderr,
            )
            return chosen

    return None


def _strip_prefix(text: str, prefix: str) -> str:
    if text.startswith(prefix):
        return text[len(prefix):]
    return text


def _find_html_report(deriv_root: Path, meta: ModelMeta) -> Optional[Path]:
    """Locate the model HTML report, tolerating FitLins naming/casing variations."""
    reports_dir = deriv_root / "reports"
    exact = reports_dir / f"model-{meta.json_name}.html"
    if exact.exists():
        return exact

    candidates = sorted(reports_dir.glob("model-*.html"))
    if not candidates:
        return None

    expected = re.sub(r"[^a-z0-9]+", "", meta.json_name.lower())
    stem_expected = re.sub(r"[^a-z0-9]+", "", meta.stem.lower())

    scored = []
    for candidate in candidates:
        base = _strip_prefix(candidate.stem, "model-")
        norm = re.sub(r"[^a-z0-9]+", "", base.lower())
        score = 0
        if norm == expected:
            score = 100
        elif expected and expected in norm:
            score = 90
        elif norm == stem_expected:
            score = 80
        elif stem_expected and stem_expected in norm:
            score = 70
        scored.append((score, candidate))

    scored.sort(key=lambda item: (-item[0], str(item[1])))
    if scored and scored[0][0] > 0:
        chosen = scored[0][1]
        print(
            f"  INFO: using HTML report '{chosen.name}' for model '{meta.stem}'",
            file=sys.stderr,
        )
        return chosen

    if len(candidates) == 1:
        chosen = candidates[0]
        print(
            f"  INFO: falling back to sole HTML report '{chosen.name}' for model '{meta.stem}'",
            file=sys.stderr,
        )
        return chosen

    return None


def generate_report(task_group: str, model_stems: list, work_root: str,
                    kernel: int, thr_suffix: str, output_pdf: str,
                    cfg_root: Optional[str] = None):
    global _WORK_ROOT_FOR_RESOLVE, _TASK_GROUP_FOR_RESOLVE

    work = Path(work_root)
    out  = Path(output_pdf)
    out.parent.mkdir(parents=True, exist_ok=True)

    _WORK_ROOT_FOR_RESOLVE = work   # used by _load_png for path fallback
    _TASK_GROUP_FOR_RESOLVE = task_group

    # Config root: server-side or local repo copy
    cfg_base = Path(cfg_root) if cfg_root else work / "fitlins_configs"

    # Load model metadata ────────────────────────────────────────────────────
    metas = []
    for stem in model_stems:
        jp = work / "fitlins_models" / task_group / f"{stem}_smdl.json"
        if not jp.exists():
            sys.exit(f"ERROR: model JSON not found: {jp}")
        meta = load_model_meta(jp, task_group, stem)
        metas.append(meta)
        print(f"  Loaded {stem}  "
              f"({len(meta.subjects)} subj, tasks={meta.tasks}, "
              f"contrasts={meta.contrasts}, lat={meta.is_lateralization})")

    # Load config files ──────────────────────────────────────────────────────
    all_configs = {}   # stem → ConfigParams
    for meta in metas:
        cp = cfg_base / task_group / f"{meta.stem}.cfg"
        all_configs[meta.stem] = load_config_file(cp)

    # Load manifests ─────────────────────────────────────────────────────────
    all_manifests = {}   # stem → {node_name: DataFrame}
    for meta in metas:
        fig_root       = work / "figures" / task_group / f"{meta.stem}_s{kernel}"
        node_manifests = {}
        for node in [meta.run_node] + meta.group_nodes:
            mp = _find_manifest(fig_root, node, thr_suffix)
            if mp is None:
                print(f"  WARNING: no manifest found for node '{node}' under {fig_root}",
                      file=sys.stderr)
                continue
            df = load_manifest(mp)
            if df is not None:
                node_manifests[node] = df
        all_manifests[meta.stem] = node_manifests

    # Parse HTML reports / scan run-report figures ──────────────────────────
    all_run_entries = {}   # stem → list[RunEntry]
    for meta in metas:
        deriv_root = work / "fitlins_derivatives" / task_group / f"{meta.stem}_s{kernel}"
        hp = _find_html_report(deriv_root, meta)

        entries = []
        if hp is not None:
            print(f"  Parsing {hp.name} ...", end=" ", flush=True)
            entries = parse_html_report(hp)
            print(f"{len(entries)} run entries from HTML.")
        else:
            print(f"  WARNING: no HTML report found under {deriv_root / 'reports'}",
                  file=sys.stderr)

        file_entries = collect_run_entries_from_report_files(deriv_root)
        if file_entries:
            merged = {(e.subject, e.task, e.run): e for e in entries}
            for e in file_entries:
                key = (e.subject, e.task, e.run)
                if key not in merged:
                    merged[key] = e
                    continue
                if merged[key].design_img is None and e.design_img is not None:
                    merged[key].design_img = e.design_img
                if merged[key].corr_img is None and e.corr_img is not None:
                    merged[key].corr_img = e.corr_img
            entries = list(merged.values())
            print(f"  Collected {len(file_entries)} run entries from report figures.")

        all_run_entries[meta.stem] = entries

    # Build PDF ──────────────────────────────────────────────────────────────
    print(f"Writing {out} ...")
    with PdfPages(str(out)) as pdf:
        write_cover_page(pdf, task_group, metas, thr_suffix, kernel)

        for meta in metas:
            print(f"  Model: {meta.stem}")
            cfg         = all_configs[meta.stem]
            node_mans   = all_manifests[meta.stem]
            run_entries = all_run_entries[meta.stem]

            write_model_card_page(pdf, meta, cfg)                   # methods card
            write_group_level_pages(pdf, meta, node_mans)           # dataset/subject
            write_matrix_pages(pdf, run_entries, meta, "design")    # design matrices
            write_matrix_pages(pdf, run_entries, meta, "corr")      # correlation matrices
            write_run_level_pages(pdf, meta, node_mans)             # run-level PNGs

    print("Done.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Generate a PDF model report from FitLins outputs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            The threshold suffix must match the directory name used by
            plot_fmri_statmaps.py, without the node prefix, e.g.:
              p-unc_p0.001_2s   → runLevel_p-unc_p0.001_2s / datasetLevel_p-unc_p0.001_2s
              fdr_a0.05_2s
              fixed_t3.1_1s

            Example:
              python generate_model_report.py \\
                --task-group ol \\
                --models ol_visual_vs_baseline ol_motor_lateralization \\
                --work-root /data/sld/homes/vguigon/work \\
                --kernel 4 \\
                --thr-suffix p-unc_p0.001_2s \\
                --output /data/sld/homes/vguigon/work/figures/ol/ol_report.pdf
        """),
    )
    p.add_argument("--task-group",  required=True,
                   help="Task group label, e.g. ol / tmth / sra")
    p.add_argument("--models",      required=True, nargs="+",
                   help="One or more model stems (without _smdl.json)")
    p.add_argument("--work-root",   required=True,
                   help="Server work root, e.g. /data/sld/homes/vguigon/work")
    p.add_argument("--kernel",      type=int, default=4,
                   help="Smoothing kernel in mm (default: 4)")
    p.add_argument("--thr-suffix",  required=True,
                   help='Threshold dir suffix, e.g. "p-unc_p0.001_2s"')
    p.add_argument("--cfg-root",    default=None,
                   help="Root directory containing fitlins_configs/{task_group}/*.cfg "
                        "(default: {work_root}/fitlins_configs)")
    p.add_argument("--output",      required=True,
                   help="Output PDF path")
    args = p.parse_args()

    generate_report(
        task_group=args.task_group,
        model_stems=args.models,
        work_root=args.work_root,
        kernel=args.kernel,
        thr_suffix=args.thr_suffix,
        output_pdf=args.output,
        cfg_root=args.cfg_root,
    )


if __name__ == "__main__":
    main()
