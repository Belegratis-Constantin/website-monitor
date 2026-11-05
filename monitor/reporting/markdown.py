"""Markdown report — also used as the GitHub Actions job summary."""

from __future__ import annotations

from monitor.models import RunReport, Status


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: RunReport, *, max_details: int = 15) -> str:
    counts = report.counts()
    site = report.site
    lines = [
        f"# {report.status.icon} {site.get('name')} — {report.status.value.upper()}",
        "",
        f"- **URL:** {site.get('base_url')}",
        f"- **Started:** {report.started_at} UTC · **Duration:** {report.duration_ms / 1000:.1f} s"
        f" · **HTTP requests:** {report.http_requests}",
        f"- **Checks:** {', '.join(report.checks_run)}",
        "- **Results:** " + " · ".join(f"{Status(k).icon} {k} {v}" for k, v in counts.items() if v),
        "",
    ]
    problems = [r for r in report.results if r.status.rank > 0]
    if problems:
        lines += ["## Problems", "", "| | Check | Target | Message |", "|---|---|---|---|"]
        lines += [f"| {r.status.icon} | {r.check} | {_cell(r.target)} | {_cell(r.message)} |"
                  for r in sorted(problems, key=lambda r: -r.status.rank)]
        lines.append("")
    lines += ["## All results", "", "| | Check | Target | Message | ms |", "|---|---|---|---|---|"]
    lines += [f"| {r.status.icon} | {r.check} | {_cell(r.target)} | {_cell(r.message)} | {r.duration_ms or ''} |"
              for r in report.results]
    detailed = [r for r in problems if r.details]
    if detailed:
        lines += ["", "## Details", ""]
        for r in detailed:
            lines.append(f"<details><summary>{r.status.icon} <b>{r.check}</b> — {r.target}</summary>\n")
            lines += [f"- {_cell(d)}" for d in r.details[:max_details]]
            if len(r.details) > max_details:
                lines.append(f"- … {len(r.details) - max_details} more in report.json")
            lines += [f"- artifact: `{a}`" for a in r.artifacts]
            lines.append("\n</details>")
    return "\n".join(lines) + "\n"
