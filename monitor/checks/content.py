"""Required and forbidden text in the served HTML.

Options:
  required:   {path: [phrase, ...]}  phrases that must appear (case-insensitive)
  forbidden:  [phrase, ...]          must not appear on any monitored page
  forbidden_patterns: [regex, ...]   same, as regular expressions
  include_markup: search raw HTML instead of visible text (default false)

For text injected by JavaScript use the browser check's ``expect_text``.
"""

from __future__ import annotations

import re

import requests

from monitor.http import describe_error
from monitor.models import CheckResult, Status

DEFAULT_FORBIDDEN = ["Lorem ipsum", "Traceback (most recent call last)"]
# PHP prints "<b>Warning</b>:  foo() in /path/file.php on line 12" when display_errors is on.
DEFAULT_PATTERNS = [r"\b(Fatal error|Parse error|Warning|Notice|Deprecated)\s*:.{0,300}? on line \d+"]


def run(ctx, options):
    required: dict[str, list[str]] = options.get("required", {}) or {}
    forbidden = list(options.get("forbidden", DEFAULT_FORBIDDEN))
    patterns = [re.compile(p) for p in options.get("forbidden_patterns", DEFAULT_PATTERNS)]
    markup = bool(options.get("include_markup", False))
    paths = list(dict.fromkeys(list(ctx.paths) + list(required)))

    results = []
    for path in paths:
        url = ctx.url(path)
        try:
            page = ctx.http.page(url)
        except requests.RequestException as exc:
            results.append(CheckResult("content", url, Status.FAIL, describe_error(exc)))
            continue
        if page.status >= 400:
            results.append(CheckResult("content", url, Status.FAIL, f"HTTP {page.status}"))
            continue
        haystack = page.response.text if markup else page.text
        folded = haystack.casefold()
        missing = [p for p in required.get(path, []) or [] if p.casefold() not in folded]
        found = [p for p in forbidden if p.casefold() in folded]
        found += [p.pattern for p in patterns if p.search(haystack)]
        details = [f"missing: {m}" for m in missing] + [f"forbidden text present: {f}" for f in found]
        if details:
            results.append(CheckResult("content", url, Status.FAIL if found else Status.WARNING,
                                       "; ".join(details[:4]), details=details))
        else:
            n = len(required.get(path, []) or [])
            results.append(CheckResult("content", url, Status.PASS,
                                       f"{n} expected phrase(s) present, no forbidden text"))
    return results
