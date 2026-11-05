"""Crawl internal pages and verify every link and referenced resource.

Each unique URL is requested once per run. Internal HTML links that return
the server's catch-all page (HTTP 200 + homepage) are reported as soft 404s.

Options:
  max_pages:        pages to crawl (default 25)
  max_links:        unique URLs to verify (default 300)
  check_external:   also verify external links (default false)
  external_timeout: seconds (default 10)
  check_fragments:  verify same-page #anchors exist (default true)
  ignore:           substrings/regexes of URLs to skip
  severity:         warning|fail for broken internal links (default warning)
"""

from __future__ import annotations

import re
from collections import defaultdict, deque

import requests

from monitor.checks._html import element_ids, extract_refs
from monitor.http import describe_error, same_site
from monitor.models import CheckResult, Status, parse_severity

# External sites that refuse bots: treat these codes as "unverifiable", not broken.
BOT_BLOCK_CODES = {401, 403, 405, 429, 999}


def run(ctx, options):
    severity = parse_severity(options.get("severity"))
    max_pages = int(options.get("max_pages", 25))
    max_links = int(options.get("max_links", 300))
    check_external = bool(options.get("check_external", False))
    check_fragments = bool(options.get("check_fragments", True))
    ignore = [re.compile(p) for p in options.get("ignore", [])]

    queue = deque(ctx.url(p) for p in ctx.paths)
    visited: set[str] = set()
    referrers: dict[str, set[str]] = defaultdict(set)
    kinds: dict[str, str] = {}
    internal_issues: list[str] = []
    external_issues: list[str] = []

    while queue and len(visited) < max_pages:
        page_url = queue.popleft()
        if page_url in visited:
            continue
        visited.add(page_url)
        try:
            page = ctx.http.page(page_url)
        except requests.RequestException as exc:
            internal_issues.append(f"{page_url}: {describe_error(exc)}")
            continue
        if page.status >= 400 or not page.is_html:
            continue  # reported by uptime / when verified as a link target
        soup = page.soup
        if check_fragments:
            ids = element_ids(soup)
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if href.startswith("#") and len(href) > 1 and href[1:] not in ids:
                    internal_issues.append(f"{page_url}: anchor {href} has no matching id")
        for ref in extract_refs(soup, page.final_url):
            if any(p.search(ref.url) for p in ignore):
                continue
            referrers[ref.url].add(page_url)
            kinds.setdefault(ref.url, ref.kind)
            if ref.kind == "link" and same_site(ref.url, ctx.base_url) and ref.url not in visited:
                queue.append(ref.url)

    targets = list(referrers)
    internal = [u for u in targets if same_site(u, ctx.base_url)]
    external = [u for u in targets if not same_site(u, ctx.base_url)] if check_external else []
    to_check = (internal + external)[:max_links]
    skipped = len(internal) + len(external) - len(to_check)

    for url in to_check:
        is_internal = same_site(url, ctx.base_url)
        source = sorted(referrers[url])[0]
        more = f" (+{len(referrers[url]) - 1} more pages)" if len(referrers[url]) > 1 else ""
        try:
            if is_internal:
                response = ctx.http.get(url) if kinds[url] == "link" else ctx.http.head_or_get(url)
            else:
                response = ctx.http.request("GET", url, stream=True, allow_redirects=True,
                                            timeout=float(options.get("external_timeout", 10)))
                response.close()
        except requests.RequestException as exc:
            (internal_issues if is_internal else external_issues).append(
                f"{url}: {describe_error(exc)} ← {source}{more}")
            continue
        code = response.status_code
        if is_internal:
            if code >= 400:
                internal_issues.append(f"{url} → HTTP {code} ← {source}{more}")
            elif kinds[url] == "link" and ctx.http.is_soft404(url, response):
                internal_issues.append(f"{url} → soft 404 (server returned its fallback page) ← {source}{more}")
        elif code >= 400 and code not in BOT_BLOCK_CODES:
            external_issues.append(f"{url} → HTTP {code} ← {source}{more}")

    metrics = {"pages_crawled": len(visited), "urls_checked": len(to_check), "urls_skipped": skipped,
               "internal_urls": len(internal)}
    summary = f"crawled {len(visited)} page(s), verified {len(to_check)} unique URL(s)"
    if skipped:
        summary += f" ({skipped} skipped: max_links)"
    results = []
    if internal_issues:
        results.append(CheckResult("links", ctx.base_url, severity,
                                   f"{len(internal_issues)} broken internal link(s)/resource(s); {summary}",
                                   details=internal_issues, metrics=metrics))
    else:
        results.append(CheckResult("links", ctx.base_url, Status.PASS,
                                   f"No broken internal links; {summary}", metrics=metrics))
    if check_external:
        results.append(CheckResult(
            "links", "external links",
            Status.WARNING if external_issues else Status.PASS,
            f"{len(external_issues)} broken external link(s)" if external_issues
            else f"{len(external)} external link(s) OK", details=external_issues))
    return results
