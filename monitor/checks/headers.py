"""HTTP security headers: presence, sane values, information leakage, cookies.

Options:
  paths:           pages to inspect (default: first profile path)
  ignore:          rule ids to skip: hsts, csp, nosniff, frame, referrer,
                   permissions, server_version, powered_by, cookies, coop
  hsts_min_age:    seconds (default 15552000 = 180 days)
  severity:        warning|fail (default warning)
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import requests

from monitor.http import describe_error
from monitor.models import CheckResult, Status, issues_result, parse_severity

FIX_HINTS = {
    "hsts": 'Strict-Transport-Security: max-age=31536000; includeSubDomains',
    "csp": "Content-Security-Policy (start with Content-Security-Policy-Report-Only)",
    "nosniff": "X-Content-Type-Options: nosniff",
    "frame": "X-Frame-Options: SAMEORIGIN or CSP frame-ancestors 'self'",
    "referrer": "Referrer-Policy: strict-origin-when-cross-origin",
    "permissions": "Permissions-Policy: camera=(), microphone=(), geolocation=()",
    "coop": "Cross-Origin-Opener-Policy: same-origin",
}


def _inspect(response: requests.Response, url: str, ignore: set[str], hsts_min: int) -> list[str]:
    h = response.headers
    issues: list[str] = []

    def rule(rule_id: str, message: str) -> None:
        if rule_id not in ignore:
            hint = FIX_HINTS.get(rule_id)
            issues.append(f"{message}" + (f" — add {hint}" if hint else ""))

    if urlparse(url).scheme == "https":
        hsts = h.get("strict-transport-security")
        if not hsts:
            rule("hsts", "HSTS header missing")
        else:
            m = re.search(r"max-age=(\d+)", hsts)
            if not m or int(m.group(1)) < hsts_min:
                rule("hsts", f"HSTS max-age too low ({hsts})")
    csp = h.get("content-security-policy", "")
    if not csp and not h.get("content-security-policy-report-only"):
        rule("csp", "Content-Security-Policy missing")
    if h.get("x-content-type-options", "").lower() != "nosniff":
        rule("nosniff", "X-Content-Type-Options is not 'nosniff'")
    if h.get("x-frame-options", "").upper() not in ("DENY", "SAMEORIGIN") and "frame-ancestors" not in csp:
        rule("frame", "No clickjacking protection")
    if not h.get("referrer-policy"):
        rule("referrer", "Referrer-Policy missing")
    if not h.get("permissions-policy"):
        rule("permissions", "Permissions-Policy missing")
    if not h.get("cross-origin-opener-policy"):
        rule("coop", "Cross-Origin-Opener-Policy missing")
    server = h.get("server", "")
    if re.search(r"\d", server):
        rule("server_version", f"Server header reveals version: '{server}' (ServerTokens Prod)")
    if h.get("x-powered-by"):
        rule("powered_by", f"X-Powered-By reveals stack: '{h['x-powered-by']}'")
    for cookie in response.raw.headers.getlist("Set-Cookie") if hasattr(response.raw, "headers") else []:
        name = cookie.split("=", 1)[0]
        lower = cookie.lower()
        missing = [flag for flag in ("secure", "httponly", "samesite") if flag not in lower]
        if missing:
            rule("cookies", f"cookie '{name}' lacks {', '.join(missing)}")
    return issues


def run(ctx, options):
    severity = parse_severity(options.get("severity"))
    ignore = set(options.get("ignore", []))
    hsts_min = int(options.get("hsts_min_age", 15552000))
    results = []
    for path in options.get("paths", ctx.paths[:1]):
        url = ctx.url(path)
        try:
            response = ctx.http.get(url)
        except requests.RequestException as exc:
            results.append(CheckResult("headers", url, Status.FAIL, describe_error(exc)))
            continue
        issues = _inspect(response, url, ignore, hsts_min)
        results.append(issues_result("headers", url, issues, "All checked security headers are set",
                                     severity=severity,
                                     summary=f"{len(issues)} header issue(s)" if issues else None))
    return results
