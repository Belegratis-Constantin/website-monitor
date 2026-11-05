"""Check registry.

Every check is a module exposing ``run(ctx, options) -> list[CheckResult]``.
Modules are imported lazily so HTTP-only runs don't need Playwright.
To add a check: create the module and register it here.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from types import ModuleType


@dataclass(frozen=True)
class CheckInfo:
    module: str
    description: str
    browser: bool = False


CHECKS: dict[str, CheckInfo] = {
    "uptime": CheckInfo("monitor.checks.uptime", "HTTP status, response time and unexpected redirects per path"),
    "ssl": CheckInfo("monitor.checks.ssl_check", "TLS certificate validity, expiry, hostname and protocol version"),
    "redirects": CheckInfo("monitor.checks.redirects", "HTTPS enforcement, canonical host and configured redirect rules"),
    "links": CheckInfo("monitor.checks.links", "Crawl internal pages; broken links/resources (incl. soft 404s) and anchors"),
    "assets": CheckInfo("monitor.checks.assets", "Same-origin CSS/JS/images/fonts load with the right MIME type"),
    "headers": CheckInfo("monitor.checks.headers", "Security headers, their values, info leakage and cookie flags"),
    "seo": CheckInfo("monitor.checks.seo", "Titles, descriptions, canonicals, Open Graph, robots.txt, sitemap, soft 404"),
    "content": CheckInfo("monitor.checks.content", "Required and forbidden text per page"),
    "accessibility": CheckInfo("monitor.checks.accessibility", "Static HTML accessibility heuristics (labels, alt, names, headings)"),
    "exposure": CheckInfo("monitor.checks.exposure", "Sensitive files/directories are not publicly readable"),
    "performance": CheckInfo("monitor.checks.performance", "TTFB, HTML size, compression and cache headers for static assets"),
    "form_endpoint": CheckInfo("monitor.checks.form_endpoint", "Live probes of a form backend that can never deliver a message"),
    "browser": CheckInfo("monitor.checks.browser", "Chromium smoke test per page and viewport (JS errors, failed requests, layout)", True),
    "forms": CheckInfo("monitor.checks.forms", "Fill and submit forms in Chromium with mocked or honeypot-guarded backends", True),
    "interactions": CheckInfo("monitor.checks.interactions", "Scripted click/fill/expect journeys (menus, toggles, …)", True),
    "axe": CheckInfo("monitor.checks.axe", "axe-core WCAG audit of the rendered pages", True),
}


def available_checks() -> list[str]:
    return list(CHECKS)


def get_check(name: str) -> ModuleType:
    if name not in CHECKS:
        raise ValueError(f"Unknown check '{name}'. Available: {', '.join(CHECKS)}")
    return import_module(CHECKS[name].module)
