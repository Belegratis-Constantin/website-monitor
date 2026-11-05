"""Same-origin assets (CSS, JS, images, fonts) load with a sensible MIME type.

Fonts and images referenced from stylesheets via ``url(...)`` are included.
A JavaScript module served with the wrong Content-Type is silently refused
by browsers, so MIME mismatches are reported as failures.

Options:
  max_assets:       default 200
  max_asset_kb:     warn for single assets larger than this (default 1500)
  check_mime:       default true
  severity:         warning|fail for missing assets (default fail)
"""

from __future__ import annotations

from urllib.parse import urlparse

import requests

from monitor.checks._html import css_refs, extract_refs
from monitor.http import describe_error, same_site
from monitor.models import CheckResult, Status, parse_severity

EXPECTED_MIME = {
    ".css": ("text/css",),
    ".js": ("javascript", "ecmascript"),
    ".mjs": ("javascript",),
    ".json": ("json",),
    ".png": ("image/png",),
    ".jpg": ("image/jpeg",), ".jpeg": ("image/jpeg",),
    ".gif": ("image/gif",),
    ".webp": ("image/webp",),
    ".avif": ("image/avif",),
    ".svg": ("image/svg+xml",),
    ".ico": ("icon",),
    ".woff2": ("font/woff2", "application/font-woff2", "application/octet-stream"),
    ".woff": ("font/woff", "application/font-woff", "application/octet-stream"),
    ".ttf": ("font/ttf", "application/x-font-ttf", "application/octet-stream", "font/sfnt"),
}


def _ext(url: str) -> str:
    path = urlparse(url).path.lower()
    dot = path.rfind(".")
    return path[dot:] if dot > path.rfind("/") else ""


def run(ctx, options):
    severity = parse_severity(options.get("severity"), Status.FAIL)
    limit = int(options.get("max_assets", 200))
    max_kb = int(options.get("max_asset_kb", 1500))
    check_mime = bool(options.get("check_mime", True))

    assets: dict[str, str] = {}  # url -> first page referencing it
    errors: list[str] = []
    warnings: list[str] = []
    for path in ctx.paths:
        try:
            page = ctx.http.page(path)
        except requests.RequestException as exc:
            errors.append(f"{path}: {describe_error(exc)}")
            continue
        if not page.is_html:
            continue
        for ref in extract_refs(page.soup, page.final_url):
            if ref.kind != "link" and same_site(ref.url, ctx.base_url):
                assets.setdefault(ref.url, page.url)

    checked = 0
    queue = list(assets)
    seen = set(queue)
    while queue and checked < limit:
        url = queue.pop(0)
        checked += 1
        ext = _ext(url)
        try:
            response = ctx.http.get(url) if ext == ".css" else ctx.http.head_or_get(url)
        except requests.RequestException as exc:
            errors.append(f"{url}: {describe_error(exc)} ← {assets[url]}")
            continue
        if response.status_code >= 400:
            errors.append(f"{url} → HTTP {response.status_code} ← {assets[url]}")
            continue
        ctype = response.headers.get("content-type", "").lower()
        if check_mime and ext in EXPECTED_MIME and not any(m in ctype for m in EXPECTED_MIME[ext]):
            if "text/html" in ctype:
                errors.append(f"{url} returns HTML instead of {ext} (missing file served by a fallback?)")
            else:
                errors.append(f"{url} served as '{ctype or 'no content-type'}', expected {EXPECTED_MIME[ext][0]}")
        size = response.headers.get("content-length")
        if size and size.isdigit() and int(size) > max_kb * 1024:
            warnings.append(f"{url} is {int(size) // 1024} KB (> {max_kb} KB)")
        if ext == ".css" and "css" in ctype:
            for sub in css_refs(response.text, response.url):
                if same_site(sub, ctx.base_url) and sub not in seen:
                    seen.add(sub)
                    assets[sub] = url
                    queue.append(sub)

    metrics = {"assets_found": len(seen), "assets_checked": checked}
    if errors:
        return [CheckResult("assets", ctx.base_url, severity,
                            f"{len(errors)} of {checked} asset(s) failed", details=errors + warnings, metrics=metrics)]
    if warnings:
        return [CheckResult("assets", ctx.base_url, Status.WARNING,
                            f"{checked} assets load; {len(warnings)} oversized", details=warnings, metrics=metrics)]
    return [CheckResult("assets", ctx.base_url, Status.PASS, f"All {checked} same-origin assets load",
                        metrics=metrics)]
