"""axe-core accessibility audit of the rendered pages (WCAG 2.x A/AA by default).

axe.min.js is taken from ``axe_source`` (a local file path) or from the
``axe-playwright-python`` package, which bundles it. Without either the check
is skipped.

Options:
  paths:          default: profile paths
  viewport:       default: first profile viewport
  tags:           axe rule tags (default [wcag2a, wcag2aa, wcag21a, wcag21aa])
  fail_on_impact: minor | moderate | serious | critical (default serious);
                  violations below it are warnings
  ignore_rules:   axe rule ids to skip (e.g. color-contrast)
  axe_source:     path to axe.min.js
"""

from __future__ import annotations

import json
from pathlib import Path

from monitor.models import CheckResult, Status

IMPACTS = ["minor", "moderate", "serious", "critical"]


def _axe_source(options) -> str | None:
    if options.get("axe_source"):
        path = Path(options["axe_source"]).expanduser()
        return path.read_text(encoding="utf-8") if path.exists() else None
    try:
        import axe_playwright_python
    except ImportError:
        return None
    path = Path(axe_playwright_python.__file__).parent / "axe.min.js"
    return path.read_text(encoding="utf-8") if path.exists() else None


def run(ctx, options):
    source = _axe_source(options)
    if source is None:
        return [CheckResult("axe", ctx.base_url, Status.SKIP,
                            "axe-core not available (pip install axe-playwright-python, or set axe_source)")]
    tags = options.get("tags", ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    threshold = IMPACTS.index(options.get("fail_on_impact", "serious"))
    ignore = set(options.get("ignore_rules", []))
    axe_options = {"runOnly": {"type": "tag", "values": tags},
                   "rules": {rule: {"enabled": False} for rule in ignore}}

    results = []
    for path in options.get("paths", ctx.paths):
        context, page = ctx.browser.new_page(options.get("viewport"))
        try:
            page.goto(ctx.url(path), wait_until="load")
            page.wait_for_timeout(int(options.get("settle_ms", 500)))
            page.add_script_tag(content=source)
            report = page.evaluate("opts => axe.run(document, opts)", axe_options)
        except Exception as exc:
            results.append(CheckResult("axe", path, Status.ERROR, f"axe failed: {str(exc).splitlines()[0][:200]}"))
            context.close()
            continue
        context.close()
        violations = report.get("violations", [])
        severe = [v for v in violations if IMPACTS.index(v.get("impact") or "minor") >= threshold]
        details = []
        for v in sorted(violations, key=lambda v: -IMPACTS.index(v.get("impact") or "minor")):
            nodes = v.get("nodes", [])
            sample = ", ".join(json.dumps(n.get("target", ["?"])[0]) for n in nodes[:3])
            details.append(f"[{v.get('impact')}] {v['id']}: {v['help']} — {len(nodes)} element(s), e.g. {sample} "
                           f"({v.get('helpUrl', '')})")
        metrics = {"violations": len(violations), "passes": len(report.get("passes", [])),
                   "incomplete": len(report.get("incomplete", []))}
        if not violations:
            results.append(CheckResult("axe", path, Status.PASS,
                                       f"no WCAG violations ({metrics['passes']} rules passed)", metrics=metrics))
            continue
        status = Status.FAIL if severe else Status.WARNING
        ids = ", ".join(v["id"] for v in (severe or violations)[:4])
        results.append(CheckResult("axe", path, status,
                                   f"{len(violations)} violation(s), {len(severe)} at/above "
                                   f"'{IMPACTS[threshold]}': {ids}", details=details, metrics=metrics))
    return results
