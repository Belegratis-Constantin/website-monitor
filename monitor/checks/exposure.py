"""Sensitive files and directories must not be publicly readable.

A path passes when the server refuses it (HTTP 4xx) or answers with its
catch-all fallback page. It fails when real content comes back — e.g. a
``.git/config``, a ``.env``, a PHP config printed as source, or a directory
listing. Probe sparingly: some hosts ban IPs that hit many 403s.

Options:
  use_defaults: include the built-in list below (default true)
  paths:        extra paths, either "/path" or {path, forbid: regex}
  severity:     warning|fail (default fail)
"""

from __future__ import annotations

import re

import requests

from monitor.http import describe_error
from monitor.models import CheckResult, Status, parse_severity

DEFAULT_PATHS = [
    "/.git/HEAD",
    "/.git/config",
    "/.env",
    "/.DS_Store",
    "/.htaccess",
    "/server-status",
    "/phpinfo.php",
]

# Content that proves a leak even if the status code looks harmless.
LEAK_SIGNATURES = [
    re.compile(r"^ref: refs/", re.M),
    re.compile(r"\[core\]\s*\n\s*repositoryformatversion"),
    re.compile(r"<\?php"),
    re.compile(r"^[A-Z][A-Z0-9_]{2,}=", re.M),
    re.compile(r"<title>Index of /", re.I),
    re.compile(r"Apache Server Status", re.I),
    re.compile(r"PHP Version \d", re.I),
    re.compile(r"(client_secret|password|api[_-]?key)\s*['\"]?\s*[:=]", re.I),
]


def run(ctx, options):
    severity = parse_severity(options.get("severity"), Status.FAIL)
    entries = list(DEFAULT_PATHS) if options.get("use_defaults", True) else []
    entries += list(options.get("paths", []))

    leaks, ok = [], 0
    for entry in entries:
        path, extra = (entry, None) if isinstance(entry, str) else (entry["path"], entry.get("forbid"))
        url = ctx.url(path)
        try:
            response = ctx.http.request("GET", url, allow_redirects=False)
        except requests.RequestException as exc:
            return [CheckResult("exposure", ctx.base_url, Status.WARNING,
                                f"probing stopped at {path}: {describe_error(exc)}")]
        if response.status_code >= 300:
            ok += 1
            continue
        if ctx.http.is_soft404(url, response):
            ok += 1
            continue
        body = response.text[:20000]
        signatures = LEAK_SIGNATURES + ([re.compile(extra)] if extra else [])
        hit = next((s.pattern for s in signatures if s.search(body)), None)
        if hit or response.status_code == 200:
            reason = f"matches /{hit}/" if hit else "returns content"
            leaks.append(f"{path} → HTTP {response.status_code}, {len(response.content)} bytes, {reason}")

    if leaks:
        return [CheckResult("exposure", ctx.base_url, severity, f"{len(leaks)} sensitive path(s) readable",
                            details=leaks)]
    return [CheckResult("exposure", ctx.base_url, Status.PASS, f"All {ok} sensitive path(s) are protected",
                        details=[e if isinstance(e, str) else e["path"] for e in entries])]
