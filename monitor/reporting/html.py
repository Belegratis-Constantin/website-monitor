"""Self-contained HTML report (no external assets; screenshots linked relatively)."""

from __future__ import annotations

from html import escape

from monitor.models import RunReport, Status

CSS = """
:root{--bg:#f7f7f8;--fg:#1b1b1f;--muted:#666a73;--card:#fff;--line:#e3e4e8;
--pass:#1a7f37;--warning:#9a6700;--fail:#cf222e;--skip:#6e7781;--error:#8250df}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e7ea;--muted:#9aa0aa;--card:#171a21;
--line:#2a2f3a;--pass:#3fb950;--warning:#d29922;--fail:#f85149;--skip:#8b949e;--error:#a371f7}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1100px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 4px}.meta{color:var(--muted);margin-bottom:16px}
.pills{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px}
.pill{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:4px 12px;cursor:pointer;
color:var(--fg);font:inherit}
.pill[aria-pressed=true]{outline:2px solid currentColor}
.pass{color:var(--pass)}.warning{color:var(--warning)}.fail{color:var(--fail)}.skip{color:var(--skip)}
.error{color:var(--error)}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:6px 0}
summary{padding:8px 12px;cursor:pointer;display:grid;grid-template-columns:80px 120px 1fr;gap:8px;align-items:start}
summary .msg{grid-column:3;color:var(--muted);overflow-wrap:anywhere}
summary .tgt{overflow-wrap:anywhere}
.body{padding:0 12px 12px 12px;border-top:1px solid var(--line)}
.body li{overflow-wrap:anywhere}.body img{max-width:100%;border:1px solid var(--line);border-radius:4px;margin-top:8px}
.st{font-weight:600;text-transform:uppercase;font-size:12px}
@media (max-width:640px){summary{grid-template-columns:70px 1fr}summary .msg{grid-column:1/-1}}
"""

JS = """
document.querySelectorAll('.pill').forEach(p => p.addEventListener('click', () => {
  const on = p.getAttribute('aria-pressed') !== 'true';
  document.querySelectorAll('.pill').forEach(x => x.setAttribute('aria-pressed', 'false'));
  p.setAttribute('aria-pressed', String(on));
  document.querySelectorAll('details[data-status]').forEach(d => {
    d.hidden = on && d.dataset.status !== p.dataset.filter;
  });
}));
"""


def render_html(report: RunReport) -> str:
    site = report.site
    counts = report.counts()
    order = sorted(report.results, key=lambda r: -r.status.rank)
    pills = "".join(
        f'<button class="pill {s}" data-filter="{s}" aria-pressed="false">{Status(s).icon} {s} {n}</button>'
        for s, n in counts.items() if n)
    rows = []
    for r in order:
        items = "".join(f"<li>{escape(d)}</li>" for d in r.details)
        shots = "".join(f'<a href="{escape(a)}"><img src="{escape(a)}" alt="screenshot {escape(r.target)}" '
                        f'loading="lazy"></a>' for a in r.artifacts if a.endswith(".png"))
        metrics = ", ".join(f"{escape(str(k))}={escape(str(v))}" for k, v in r.metrics.items())
        body = (f"<ul>{items}</ul>" if items else "") + (f"<p>{metrics}</p>" if metrics else "") + shots
        rows.append(
            f'<details data-status="{r.status.value}"{" open" if r.status.rank >= 2 and body else ""}>'
            f'<summary><span class="st {r.status.value}">{r.status.icon} {r.status.value}</span>'
            f'<b>{escape(r.check)}</b><span class="tgt">{escape(r.target)}</span>'
            f'<span class="msg">{escape(r.message)}</span></summary>'
            f'<div class="body">{body or "<p>No details.</p>"}</div></details>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(str(site.get('name')))} monitor report</title><style>{CSS}</style></head>
<body><main>
<h1><span class="{report.status.value}">{report.status.icon}</span> {escape(str(site.get('name')))}
 — <span class="{report.status.value}">{report.status.value.upper()}</span></h1>
<div class="meta">{escape(str(site.get('base_url')))} · {escape(report.started_at)} UTC ·
 {report.duration_ms / 1000:.1f} s · {report.http_requests} HTTP requests · checks: {escape(', '.join(report.checks_run))}</div>
<div class="pills">{pills}</div>
{''.join(rows)}
</main><script>{JS}</script></body></html>
"""
