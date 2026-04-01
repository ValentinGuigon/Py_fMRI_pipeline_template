#!/usr/bin/env python3
"""
Build a Markdown index of generated analyses and reports.

The output is designed to be:
- human-readable as a Markdown inventory
- machine-readable via a sibling JSON file

Example:
  python3 build_analysis_index.py \
    --task-group tmth \
    --work-root /data/sld/homes/vguigon/slb_work

Or, from the repository root:
  python3 scripts/report/build_analysis_index.py \
    --task-group tmth \
    --work-root /data/sld/homes/vguigon/slb_work

By default, this writes:
  {work_root}/docs/indexes/{task_group}_analysis_index.md
  {work_root}/docs/indexes/{task_group}_analysis_index.json
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _strip_suffix(text: str, suffix: str) -> str:
    if suffix and text.endswith(suffix):
        return text[:-len(suffix)]
    return text


def _parse_cfg_value(text: str, key: str) -> str:
    prefix = f"{key}="
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        value = line[len(prefix):].strip().strip("\"'")
        return value
    return ""


def _relative_to_work_root(path: Optional[Path], work_root: Path) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(work_root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _load_json(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_glm_and_contrasts(model_data: Dict[str, object]) -> Dict[str, object]:
    nodes = model_data.get("Nodes", [])
    glm = {
        "name": model_data.get("Name", ""),
        "input": model_data.get("Input", {}),
        "nodes": [],
    }
    contrasts = []

    for node in nodes:
        model = node.get("Model", {})
        transformations = node.get("Transformations", {})
        node_summary = {
            "level": node.get("Level", ""),
            "name": node.get("Name", ""),
            "group_by": node.get("GroupBy", []),
            "model_type": model.get("Type", ""),
            "design_matrix": model.get("X", []),
            "transformations": transformations.get("Instructions", []),
        }
        glm["nodes"].append(node_summary)

        for contrast in node.get("Contrasts", []):
            contrasts.append(
                {
                    "node": node.get("Name", ""),
                    "level": node.get("Level", ""),
                    "name": contrast.get("Name", ""),
                    "conditions": contrast.get("ConditionList", []),
                    "weights": contrast.get("Weights", []),
                    "test": contrast.get("Test", ""),
                }
            )

    return {
        "glm": glm,
        "contrasts": contrasts,
    }


def _summarize_glm(glm: Dict[str, object]) -> str:
    node_lines = []
    for node in glm.get("nodes", []):
        level = node.get("level", "") or "?"
        name = node.get("name", "") or "?"
        model_type = node.get("model_type", "") or "?"
        design = node.get("design_matrix", [])
        design_str = ", ".join(str(item) for item in design)
        node_lines.append(f"{level}/{name}: {model_type} [{design_str}]")
    return "<br>".join(node_lines) if node_lines else "-"


def _summarize_contrasts(contrasts: List[Dict[str, object]]) -> str:
    lines = []
    for contrast in contrasts:
        conds = contrast.get("conditions", [])
        weights = contrast.get("weights", [])
        parts = []
        for cond, weight in zip(conds, weights):
            cond_label = str(cond).replace("trial_type.", "")
            parts.append(f"{weight}:{cond_label}")
        contrast_name = contrast.get("name", "") or "?"
        node_name = contrast.get("node", "") or "?"
        test = contrast.get("test", "") or "?"
        lines.append(f"{node_name}/{contrast_name} ({test}): " + ", ".join(parts))
    return "<br>".join(lines) if lines else "-"


def _summarize_reports(entry: Dict[str, object]) -> str:
    if entry.get("pdf_reports"):
        return "<br>".join(entry["pdf_reports"])
    return "-"


def collect_entries(work_root: Path, task_group: str) -> List[Dict[str, object]]:
    models_dir = work_root / "fitlins_models" / task_group
    cfg_dir = work_root / "fitlins_configs" / task_group
    deriv_dir = work_root / "fitlins_derivatives" / task_group
    reports_dir = work_root / "reports" / task_group
    figures_dir = work_root / "figures" / task_group

    entries = []
    for model_json in sorted(models_dir.glob("*_smdl.json")):
        stem = _strip_suffix(model_json.stem, "_smdl")
        model_data = _load_json(model_json)
        model_spec = _extract_glm_and_contrasts(model_data)
        cfg_path = cfg_dir / f"{stem}.cfg"
        cfg_text = _read_text(cfg_path)
        smooth = _parse_cfg_value(cfg_text, "SMOOTH")
        kernel = smooth.split(":", 1)[0] if smooth else ""
        out_suffix = f"{stem}_s{kernel}" if kernel else stem

        thr_mode = _parse_cfg_value(cfg_text, "THR_MODE")
        p_unc = _parse_cfg_value(cfg_text, "P_UNC")
        alpha = _parse_cfg_value(cfg_text, "ALPHA")
        two_sided = _parse_cfg_value(cfg_text, "TWO_SIDED")
        cluster_extent = _parse_cfg_value(cfg_text, "CLUSTER_EXTENT")
        events_dir = _parse_cfg_value(cfg_text, "EVENTS_DIR")

        deriv_candidates = sorted(deriv_dir.glob(f"{stem}_s*/reports/model-*.html"))
        report_candidates = sorted(reports_dir.glob(f"{stem}*.pdf"))
        manifest_candidates = sorted(figures_dir.glob(f"{out_suffix}/*/manifest.tsv"))

        entries.append(
            {
                "model": stem,
                "task_group": task_group,
                "kernel_mm": kernel,
                "config_path": _relative_to_work_root(cfg_path if cfg_path.exists() else None, work_root),
                "model_json_path": _relative_to_work_root(model_json, work_root),
                "glm": model_spec["glm"],
                "contrasts_spec": model_spec["contrasts"],
                "thr_mode": thr_mode,
                "p_unc": p_unc,
                "alpha": alpha,
                "two_sided": two_sided,
                "cluster_extent_voxels": cluster_extent,
                "events_dir": events_dir,
                "events_type": "motor" if events_dir else "standard",
                "fitlins_html_reports": [
                    _relative_to_work_root(path, work_root) for path in deriv_candidates
                ],
                "pdf_reports": [
                    _relative_to_work_root(path, work_root) for path in report_candidates
                ],
                "figure_manifests": [
                    _relative_to_work_root(path, work_root) for path in manifest_candidates
                ],
                "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )

    return entries


def render_markdown(task_group: str, generated: str, entries: List[Dict[str, object]]) -> str:
    lines = [
        f"# Analysis Index: {task_group}",
        "",
        f"Generated: `{generated}`",
        "",
        "## Summary",
        "",
        f"- Models indexed: {len(entries)}",
        f"- PDF reports found: {sum(len(e['pdf_reports']) for e in entries)}",
        f"- JSON index written separately: `{task_group}_analysis_index.json`",
        "",
        "## Analyses",
        "",
        "| Model | Kernel (mm) | GLM | Contrasts | Reports |",
        "| --- | --- | --- | --- | --- |",
    ]

    for entry in entries:
        glm = _summarize_glm(entry["glm"])
        contrasts = _summarize_contrasts(entry["contrasts_spec"])
        reports = _summarize_reports(entry)
        lines.append(
            f"| `{entry['model']}` | `{entry['kernel_mm'] or '-'}` | {glm} | {contrasts} | {reports} |"
        )

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Build a Markdown analysis index.")
    parser.add_argument("--task-group", required=True, help="Task group label, e.g. tmth")
    parser.add_argument("--work-root", required=True, help="Analysis work root")
    parser.add_argument(
        "--output",
        default=None,
        help="Output Markdown file (default: {work_root}/docs/indexes/{task_group}_analysis_index.md)",
    )
    args = parser.parse_args()

    work_root = Path(args.work_root)
    if args.output:
        output = Path(args.output)
    else:
        output = work_root / "docs" / "indexes" / f"{args.task_group}_analysis_index.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    json_output = output.with_suffix(".json")

    entries = collect_entries(work_root, args.task_group)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "task_group": args.task_group,
        "generated_utc": generated,
        "work_root": str(work_root).replace("\\", "/"),
        "entries": entries,
    }
    markdown = render_markdown(args.task_group, generated, entries)
    output.write_text(markdown, encoding="utf-8")
    json_output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Wrote analysis index: {output}")
    print(f"Wrote machine-readable index: {json_output}")
    print(f"Entries: {len(entries)}")


if __name__ == "__main__":
    main()
