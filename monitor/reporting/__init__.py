"""Report writers. Each takes a RunReport and returns the written file path.

Formats: json, md, html, junit. ``write_reports`` also maintains
``reports/<site>/latest.json`` so dashboards/alerting can poll one file.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from pathlib import Path

from monitor.models import RunReport
from monitor.reporting.html import render_html
from monitor.reporting.junit import render_junit
from monitor.reporting.markdown import render_markdown

FORMATS = ("json", "md", "html", "junit")


def render_json(report: RunReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)


RENDERERS = {
    "json": ("report.json", render_json),
    "md": ("report.md", render_markdown),
    "html": ("report.html", render_html),
    "junit": ("junit.xml", render_junit),
}


def write_reports(report: RunReport, out_dir: Path, formats: Iterable[str] = FORMATS) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for fmt in formats:
        if fmt not in RENDERERS:
            raise ValueError(f"unknown report format '{fmt}' (available: {', '.join(FORMATS)})")
        filename, render = RENDERERS[fmt]
        path = out_dir / filename
        path.write_text(render(report), encoding="utf-8")
        written[fmt] = path
    if "json" in written:
        shutil.copyfile(written["json"], out_dir.parent / "latest.json")
    return written
