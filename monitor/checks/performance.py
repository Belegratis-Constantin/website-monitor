"""Transfer-level performance hygiene (browser timings live in the ``browser`` check).

Options:
  max_ttfb_ms:            default 800
  max_html_kb:            default 150 (uncompressed)
  require_compression:    text responses must be gzip/br encoded (default true)
  fingerprinted_pattern:  regex for hashed build files that should be cached long
                          (default matches Vite/Webpack names like app-3fA9_x1Q.js)
  min_cache_seconds:      for fingerprinted files (default 604800 = 7 days)
  severity:               warning|fail (default warning)
"""

from __future__ import annotations

import re

import requests

from monitor.checks._html import extract_refs
from monitor.http import describe_error, same_site
from monitor.models import issues_result, parse_severity

TEXT_TYPES = ("text/html", "text/css", "javascript", "json", "svg", "xml")


def _max_age(cache_control: str) -> int:
    m = re.search(r"max-age=(\d+)", cache_control or "")
    return int(m.group(1)) if m else 0


def run(ctx, options):
    severity = parse_severity(options.get("severity"))
    max_ttfb = int(options.get("max_ttfb_ms", 800))
    max_html = int(options.get("max_html_kb", 150)) * 1024
    need_compression = bool(options.get("require_compression", True))
    fingerprint = re.compile(options.get("fingerprinted_pattern", r"[-.][A-Za-z0-9_-]{8,}\.(?:js|css|woff2?)$"))
    min_cache = int(options.get("min_cache_seconds", 604800))

    page_issues: list[str] = []
    asset_urls: set[str] = set()
    ttfbs = []
    for path in ctx.paths:
        url = ctx.url(path)
        try:
            page = ctx.http.page(url)
        except requests.RequestException as exc:
            page_issues.append(f"{path}: {describe_error(exc)}")
            continue
        ttfb = round(page.response.elapsed.total_seconds() * 1000)
        ttfbs.append(ttfb)
        if ttfb > max_ttfb:
            page_issues.append(f"{path}: TTFB {ttfb} ms > {max_ttfb} ms")
        size = len(page.response.content)
        if size > max_html:
            page_issues.append(f"{path}: HTML is {size // 1024} KB > {max_html // 1024} KB")
        if need_compression and size > 1024 and not page.response.headers.get("content-encoding"):
            page_issues.append(f"{path}: HTML sent uncompressed ({size // 1024} KB) — enable mod_deflate/brotli")
        if page.is_html:
            asset_urls |= {r.url for r in extract_refs(page.soup, page.final_url)
                           if r.kind in ("script", "resource") and same_site(r.url, ctx.base_url)}

    asset_issues: list[str] = []
    uncompressed, short_cache = [], []
    for url in sorted(asset_urls):
        try:
            r = ctx.http.get(url)
        except requests.RequestException:
            continue  # reported by the assets check
        ctype = r.headers.get("content-type", "")
        if (need_compression and any(t in ctype for t in TEXT_TYPES) and len(r.content) > 1024
                and not r.headers.get("content-encoding")):
            uncompressed.append(url.rsplit("/", 1)[-1])
        if fingerprint.search(url):
            cc = r.headers.get("cache-control", "")
            if "immutable" not in cc and _max_age(cc) < min_cache:
                short_cache.append(f"{url.rsplit('/', 1)[-1]} ({cc or 'no Cache-Control'})")
    if uncompressed:
        asset_issues.append(f"{len(uncompressed)} text asset(s) uncompressed: {', '.join(uncompressed[:6])}")
    if short_cache:
        asset_issues.append(f"{len(short_cache)} fingerprinted asset(s) not cached long-term "
                            f"(use Cache-Control: public, max-age=31536000, immutable): {', '.join(short_cache[:4])}")

    metrics = {"avg_ttfb_ms": round(sum(ttfbs) / len(ttfbs)) if ttfbs else None, "assets": len(asset_urls)}
    return [
        issues_result("performance", f"{ctx.base_url} (pages)", page_issues,
                      f"{len(ttfbs)} page(s): TTFB ≤ {max_ttfb} ms, compressed, ≤ {max_html // 1024} KB",
                      severity=severity, metrics=metrics),
        issues_result("performance", f"{ctx.base_url} (assets)", asset_issues,
                      f"{len(asset_urls)} CSS/JS asset(s) compressed and cached", severity=severity),
    ]
