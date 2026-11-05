"""Redirect rules: HTTPS enforcement, canonical host, legacy URLs, clean URLs.

Options:
  enforce_https:  true -> http://<host>/ must redirect to https://<host>/
  canonical_host: e.g. "example.com"; ``aliases`` must redirect there
  aliases:        hostnames (www., other TLDs) that must 301 to the canonical host
  rules:          list of {from, to?, status?, expect_status?, severity?}
                  from/to may be paths or absolute URLs. Without ``to`` the
                  URL must answer ``expect_status`` (default 200) directly.
  severity:       default severity for failed rules (warning|fail, default warning)
"""

from __future__ import annotations

from urllib.parse import urlparse

import requests

from monitor.http import describe_error, join_url
from monitor.models import CheckResult, Status, parse_severity

PERMANENT = {301, 308}


def _norm(url: str) -> str:
    p = urlparse(url)
    path = p.path or "/"
    return f"{p.scheme}://{p.netloc.lower()}{path}" + (f"?{p.query}" if p.query else "")


def _check_rule(ctx, rule: dict, severity: Status) -> CheckResult:
    source = join_url(ctx.base_url, rule["from"])
    sev = parse_severity(rule.get("severity"), severity)
    try:
        response = ctx.http.request("GET", source, allow_redirects=True)
    except requests.RequestException as exc:
        return CheckResult("redirects", source, sev, describe_error(exc))

    chain = [f"{r.status_code} {r.url}" for r in response.history] + [f"{response.status_code} {response.url}"]
    metrics = {"hops": len(response.history)}
    if "to" not in rule:
        expected = int(rule.get("expect_status", 200))
        if response.history or response.status_code != expected:
            return CheckResult("redirects", source, sev,
                               f"expected direct HTTP {expected}, got: {' → '.join(chain)}", metrics=metrics)
        return CheckResult("redirects", source, Status.PASS, f"served directly with HTTP {expected}", metrics=metrics)

    target = join_url(ctx.base_url, rule["to"])
    allowed = rule.get("status", sorted(PERMANENT))
    allowed = set(allowed if isinstance(allowed, list) else [allowed])
    problems = []
    if not response.history:
        problems.append(f"no redirect (HTTP {response.status_code})")
    else:
        first = response.history[0].status_code
        if first not in allowed:
            problems.append(f"first hop is HTTP {first}, expected {'/'.join(map(str, sorted(allowed)))}")
        if _norm(response.url) != _norm(target):
            problems.append(f"ends at {response.url}, expected {target}")
        if len(response.history) > int(rule.get("max_hops", 2)):
            problems.append(f"{len(response.history)} hops (redirect chain)")
    if response.status_code >= 400:
        problems.append(f"final HTTP {response.status_code}")
    if problems:
        return CheckResult("redirects", source, sev, "; ".join(problems), details=chain, metrics=metrics)
    return CheckResult("redirects", source, Status.PASS, f"→ {response.url} ({' → '.join(chain[:-1])})",
                       metrics=metrics)


def run(ctx, options):
    severity = parse_severity(options.get("severity"))
    parsed = urlparse(ctx.base_url)
    rules: list[dict] = []
    if options.get("enforce_https") and parsed.scheme == "https":
        rules.append({"from": f"http://{parsed.netloc}/", "to": f"https://{parsed.netloc}/"})
    canonical = options.get("canonical_host", parsed.netloc)
    skipped = []
    if canonical.lower() == parsed.netloc.lower():
        for alias in options.get("aliases", []):
            rules.append({"from": f"https://{alias}/", "to": f"https://{canonical}/",
                          "severity": options.get("alias_severity")})
    elif options.get("aliases"):
        # Running against staging/localhost (--base-url): alias domains belong to production.
        skipped.append(CheckResult("redirects", ", ".join(options["aliases"]), Status.SKIP,
                                   f"alias checks skipped: base URL host is not {canonical}"))
    rules += list(options.get("rules", []))
    return skipped + [_check_rule(ctx, rule, severity) for rule in rules]
