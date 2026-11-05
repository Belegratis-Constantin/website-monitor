"""Static accessibility heuristics on the served HTML (fast, no browser).

This is not a WCAG audit — enable the ``axe`` check for that. It catches the
classic regressions cheaply: missing labels, alt texts, accessible names,
duplicate ids, skipped heading levels and positive tabindex.

Options:
  severity: warning|fail (default warning)
  ignore:   regexes; matching issues are dropped (e.g. elements that only get
            their label from JavaScript — the axe check sees the rendered DOM)
"""

from __future__ import annotations

import re
from collections import Counter

import requests

from monitor.http import describe_error
from monitor.models import CheckResult, Status, issues_result, parse_severity


def _accessible_name(el, soup) -> str:
    if el.get("aria-label", "").strip():
        return el["aria-label"].strip()
    if el.get("aria-labelledby"):
        parts = [soup.find(id=i) for i in el["aria-labelledby"].split()]
        return " ".join(p.get_text(" ", strip=True) for p in parts if p)
    text = el.get_text(" ", strip=True)
    if text:
        return text
    img = el.find("img", alt=True)
    if img and img["alt"].strip():
        return img["alt"].strip()
    return el.get("title", "").strip()


def _issues(soup) -> list[str]:
    issues: list[str] = []
    html = soup.find("html")
    if not html or not html.get("lang"):
        issues.append("document language (<html lang>) missing")

    for img in soup.find_all("img"):
        if not img.has_attr("alt"):
            issues.append(f"image without alt: {img.get('src', '?')}")

    for control in soup.find_all(["input", "select", "textarea"]):
        if control.get("type") in ("hidden", "submit", "button", "reset", "image"):
            continue
        cid = control.get("id")
        labelled = (
            (cid and soup.find("label", attrs={"for": cid}))
            or control.find_parent("label")
            or control.get("aria-label") or control.get("aria-labelledby") or control.get("title")
        )
        if not labelled:
            issues.append(f"form control without label: <{control.name} name={control.get('name')!r} id={cid!r}>")

    for button in soup.find_all("button"):
        if not _accessible_name(button, soup) and not button.has_attr("data-i18n-aria"):
            issues.append(f"button without accessible name: class={' '.join(button.get('class', []))!r}")
    for link in soup.find_all("a", href=True):
        if not _accessible_name(link, soup):
            issues.append(f"link without accessible name: href={link['href']!r}")

    dupes = [i for i, n in Counter(el["id"] for el in soup.find_all(attrs={"id": True})).items() if n > 1]
    if dupes:
        issues.append(f"duplicate id(s): {', '.join(sorted(dupes)[:8])}")

    previous = 0
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        level = int(heading.name[1])
        if previous and level > previous + 1:
            issues.append(f"heading level skipped: h{previous} → h{level} ('{heading.get_text(strip=True)[:40]}')")
        previous = level

    for el in soup.find_all(attrs={"tabindex": True}):
        try:
            if int(el["tabindex"]) > 0:
                issues.append(f"positive tabindex on <{el.name}> breaks focus order")
        except ValueError:
            pass
    if soup.find("meta", attrs={"name": "viewport", "content": lambda c: c and (
            "user-scalable=no" in c.replace(" ", "") or "maximum-scale=1" in c.replace(" ", ""))}):
        issues.append("viewport disables zoom (user-scalable=no / maximum-scale=1)")
    return issues


def run(ctx, options):
    severity = parse_severity(options.get("severity"))
    ignore = [re.compile(p) for p in options.get("ignore", [])]
    results = []
    for path in ctx.paths:
        url = ctx.url(path)
        try:
            page = ctx.http.page(url)
        except requests.RequestException as exc:
            results.append(CheckResult("accessibility", url, Status.FAIL, describe_error(exc)))
            continue
        if page.status >= 400 or not page.is_html:
            results.append(CheckResult("accessibility", url, Status.SKIP, f"HTTP {page.status}; not analysed"))
            continue
        issues = [i for i in _issues(page.soup) if not any(p.search(i) for p in ignore)]
        results.append(issues_result("accessibility", url, issues,
                                     "No static accessibility issues found", severity=severity))
    return results
