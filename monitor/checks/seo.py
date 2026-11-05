"""Search-engine basics per page plus site-wide robots.txt, sitemap and soft-404 checks.

Options:
  title_length:        [min, max] (default [10, 70])
  description_length:  [min, max] (default [50, 170])
  require_og:          default true
  check_robots:        default true
  check_sitemap:       default true (URL from robots.txt, else /sitemap.xml)
  sitemap_max_urls:    URLs from the sitemap to verify (default 50)
  check_soft_404:      unknown URLs should answer 404 (default true)
  noindex_allowed:     paths that may carry noindex
  severity:            warning|fail (default warning)
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from urllib.parse import urlparse

import requests

from monitor.http import describe_error, join_url
from monitor.models import CheckResult, Status, issues_result, parse_severity

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def _norm(url: str) -> str:
    p = urlparse(url)
    path = p.path or "/"
    return f"{p.netloc.lower()}{path}"


def _page_issues(ctx, page, options, seen_titles, seen_desc, og_images) -> list[str]:
    soup = page.soup
    issues: list[str] = []
    tmin, tmax = options.get("title_length", [10, 70])
    dmin, dmax = options.get("description_length", [50, 170])

    title = soup.title.get_text(strip=True) if soup.title else ""
    if not title:
        issues.append("missing <title>")
    elif not tmin <= len(title) <= tmax:
        issues.append(f"title length {len(title)} outside {tmin}–{tmax}: '{title}'")
    seen_titles[title].append(page.url)

    desc_tag = soup.find("meta", attrs={"name": "description"})
    desc = (desc_tag.get("content") or "").strip() if desc_tag else ""
    if not desc:
        issues.append("missing meta description")
    elif not dmin <= len(desc) <= dmax:
        issues.append(f"meta description length {len(desc)} outside {dmin}–{dmax}")
    if desc:
        seen_desc[desc].append(page.url)

    canonical = soup.find("link", rel="canonical")
    if not canonical or not canonical.get("href"):
        issues.append("missing canonical link")
    else:
        href = canonical["href"]
        if not href.startswith(("http://", "https://")):
            issues.append(f"canonical is not absolute: {href}")
        elif _norm(href) != _norm(page.final_url) and _norm(href).rstrip("/") != _norm(page.final_url).rstrip("/"):
            issues.append(f"canonical points elsewhere: {href}")

    h1s = soup.find_all("h1")
    if not h1s:
        issues.append("no <h1> in the HTML source")
    elif len(h1s) > 1:
        issues.append(f"{len(h1s)} <h1> elements")
    html = soup.find("html")
    if not html or not html.get("lang"):
        issues.append("missing <html lang>")
    if not soup.find("meta", attrs={"name": "viewport"}):
        issues.append("missing viewport meta tag")

    robots = soup.find("meta", attrs={"name": "robots"})
    if robots and "noindex" in robots.get("content", "").lower():
        if urlparse(page.url).path not in options.get("noindex_allowed", []):
            issues.append("page is marked noindex")

    if options.get("require_og", True):
        for prop in ("og:title", "og:description", "og:image", "og:url"):
            if not soup.find("meta", property=prop):
                issues.append(f"missing {prop}")
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            og_images[og_image["content"]].append(page.url)
    return issues


def _robots_and_sitemap(ctx, options) -> list[CheckResult]:
    results: list[CheckResult] = []
    sitemap_urls = []
    if options.get("check_robots", True):
        url = ctx.url("/robots.txt")
        try:
            r = ctx.http.get(url)
            issues = []
            if r.status_code != 200 or "html" in r.headers.get("content-type", ""):
                issues.append(f"robots.txt not served as text (HTTP {r.status_code}, {r.headers.get('content-type')})")
            else:
                body = r.text
                sitemap_urls = [line.split(":", 1)[1].strip() for line in body.splitlines()
                                if line.lower().startswith("sitemap:")]
                agent_all, blocks_all = False, False
                for line in body.splitlines():
                    key, _, value = line.partition(":")
                    key, value = key.strip().lower(), value.strip()
                    if key == "user-agent":
                        agent_all = value == "*"
                    elif key == "disallow" and agent_all and value == "/":
                        blocks_all = True
                if blocks_all:
                    issues.append("robots.txt disallows the whole site for all crawlers")
                if not sitemap_urls:
                    issues.append("robots.txt has no Sitemap: line")
            results.append(issues_result("seo", url, issues, "robots.txt OK"))
        except requests.RequestException as exc:
            results.append(CheckResult("seo", url, Status.WARNING, describe_error(exc)))

    if options.get("check_sitemap", True):
        url = sitemap_urls[0] if sitemap_urls else ctx.url("/sitemap.xml")
        try:
            r = ctx.http.get(url)
            if r.status_code != 200:
                results.append(CheckResult("seo", url, Status.WARNING, f"sitemap HTTP {r.status_code}"))
                return results
            root = ET.fromstring(r.content)
            locs = [el.text.strip() for el in root.iter(f"{SITEMAP_NS}loc") if el.text]
            issues = []
            listed = {_norm(u) for u in locs}
            for path in ctx.paths:
                if _norm(ctx.url(path)) not in listed:
                    issues.append(f"{path} is monitored but not in the sitemap")
            for loc in locs[: int(options.get("sitemap_max_urls", 50))]:
                if urlparse(loc).netloc.lower() != urlparse(ctx.base_url).netloc.lower():
                    issues.append(f"{loc}: different host than the site")
                    continue
                try:
                    resp = ctx.http.get(loc)
                except requests.RequestException as exc:
                    issues.append(f"{loc}: {describe_error(exc)}")
                    continue
                if resp.status_code != 200:
                    issues.append(f"{loc} → HTTP {resp.status_code}")
                elif resp.history:
                    issues.append(f"{loc} redirects to {resp.url} (list the final URL)")
                elif ctx.http.is_soft404(loc, resp):
                    issues.append(f"{loc} → soft 404")
            results.append(issues_result("seo", url, issues, f"sitemap lists {len(locs)} URL(s), all reachable",
                                         metrics={"sitemap_urls": len(locs)}))
        except ET.ParseError as exc:
            results.append(CheckResult("seo", url, Status.WARNING, f"sitemap is not valid XML: {exc}"))
        except requests.RequestException as exc:
            results.append(CheckResult("seo", url, Status.WARNING, describe_error(exc)))
    return results


def run(ctx, options):
    severity = parse_severity(options.get("severity"))
    results = []
    seen_titles: dict[str, list[str]] = defaultdict(list)
    seen_desc: dict[str, list[str]] = defaultdict(list)
    og_images: dict[str, list[str]] = defaultdict(list)

    for path in ctx.paths:
        url = ctx.url(path)
        try:
            page = ctx.http.page(url)
        except requests.RequestException as exc:
            results.append(CheckResult("seo", url, Status.FAIL, describe_error(exc)))
            continue
        if page.status >= 400 or not page.is_html:
            results.append(CheckResult("seo", url, Status.SKIP, f"HTTP {page.status}; not analysed"))
            continue
        issues = _page_issues(ctx, page, options, seen_titles, seen_desc, og_images)
        results.append(issues_result("seo", url, issues, "SEO metadata OK", severity=severity))

    site_issues = []
    for title, urls in seen_titles.items():
        if title and len(urls) > 1:
            site_issues.append(f"duplicate title '{title}' on {', '.join(urls)}")
    for _desc, urls in seen_desc.items():
        if len(urls) > 1:
            site_issues.append(f"duplicate meta description on {', '.join(urls)}")
    for image in og_images:
        try:
            r = ctx.http.head_or_get(join_url(ctx.base_url, image))
            if r.status_code >= 400 or not r.headers.get("content-type", "").startswith("image/"):
                site_issues.append(f"og:image {image} → HTTP {r.status_code} {r.headers.get('content-type')}")
        except requests.RequestException as exc:
            site_issues.append(f"og:image {image}: {describe_error(exc)}")
    if options.get("check_soft_404", True) and ctx.http.soft404_fingerprint():
        site_issues.append("unknown URLs return HTTP 200 with a fallback page (soft 404) — "
                           "search engines may index junk URLs and broken links stay invisible; "
                           "serve a real 404 page (ErrorDocument 404)")
    results.append(issues_result("seo", f"{ctx.base_url} (site-wide)", site_issues,
                                 "No duplicate titles/descriptions; og:images load; real 404s", severity=severity))
    results += _robots_and_sitemap(ctx, options)
    return results
