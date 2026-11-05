"""Live probes of a form backend that are designed to never deliver a message.

Most mail handlers reject a request long before they send anything: wrong
method, foreign Origin, honeypot filled, submitted too fast, invalid email.
Each of those guards is a cheap, mail-free way to prove that the endpoint is
deployed, PHP (or whatever) executes, its config loads and its spam
protection still works. Every POST probe must document *why* it cannot
deliver (``safe_because``) — config validation enforces that.

Options:
  url:      endpoint path or URL (default /api/send.php)
  probes:   list of
              name:           label
              method:         GET | POST | ... (default POST)
              headers:        extra headers ({origin} etc. are expanded)
              data:           form fields (urlencoded)
              json:           JSON body instead of data
              expect_status:  int or list
              expect_json:    subset the JSON response must contain
              expect_body_contains: text
              expect_content_type:  substring (default "json" when expect_json set)
              max_response_ms: warn when slower
              safe_because:   required for non-GET methods
  severity: warning|fail (default fail)

Placeholders inside header/data values:
  {base_url} {origin} {host}   from the profile
  {now_ms} / {now_ms:-10}      current epoch milliseconds, optionally shifted by N seconds
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

import requests

from monitor.http import describe_error, join_url
from monitor.models import CheckResult, Status, parse_severity

_NOW = re.compile(r"\{now_ms(?::(-?\d+))?\}")


def expand(value: Any, base_url: str) -> Any:
    if isinstance(value, dict):
        return {k: expand(v, base_url) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, base_url) for v in value]
    if not isinstance(value, str):
        return value
    parsed = urlparse(base_url)
    value = _NOW.sub(lambda m: str(int((time.time() + int(m.group(1) or 0)) * 1000)), value)
    return (value.replace("{base_url}", base_url)
                 .replace("{origin}", f"{parsed.scheme}://{parsed.netloc}")
                 .replace("{host}", parsed.netloc))


def json_subset_mismatches(expected: Any, actual: Any, path: str = "$") -> list[str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected object, got {type(actual).__name__}"]
        out = []
        for key, value in expected.items():
            if key not in actual:
                out.append(f"{path}.{key}: missing")
            else:
                out += json_subset_mismatches(value, actual[key], f"{path}.{key}")
        return out
    return [] if expected == actual else [f"{path}: expected {expected!r}, got {actual!r}"]


def _run_probe(ctx, endpoint: str, probe: dict, severity: Status) -> CheckResult:
    name = probe.get("name", "probe")
    method = str(probe.get("method", "POST")).upper()
    url = join_url(ctx.base_url, expand(probe.get("url", endpoint), ctx.base_url))
    target = f"{urlparse(url).path} [{name}]"
    kwargs: dict[str, Any] = {"allow_redirects": False,
                              "headers": expand(probe.get("headers", {}), ctx.base_url)}
    if "json" in probe:
        kwargs["json"] = expand(probe["json"], ctx.base_url)
    elif "data" in probe:
        kwargs["data"] = expand(probe["data"], ctx.base_url)

    started = time.perf_counter()
    try:
        response = ctx.http.request(method, url, **kwargs)
    except requests.RequestException as exc:
        return CheckResult("form_endpoint", target, severity, describe_error(exc))
    elapsed = round((time.perf_counter() - started) * 1000)

    expected = probe["expect_status"]
    expected = set(expected if isinstance(expected, list) else [expected])
    problems = []
    if response.status_code not in expected:
        problems.append(f"HTTP {response.status_code}, expected {'/'.join(map(str, sorted(expected)))}")
    ctype_expect = probe.get("expect_content_type", "json" if "expect_json" in probe else None)
    ctype = response.headers.get("content-type", "")
    if ctype_expect and ctype_expect not in ctype:
        problems.append(f"Content-Type '{ctype}' does not contain '{ctype_expect}'")
    body_snippet = response.text[:300].replace("\n", " ")
    if "expect_json" in probe:
        try:
            problems += json_subset_mismatches(probe["expect_json"], response.json())
        except ValueError:
            problems.append(f"response is not JSON (PHP notice/fatal error in output?): {body_snippet!r}")
    if probe.get("expect_body_contains") and probe["expect_body_contains"] not in response.text:
        problems.append(f"body lacks {probe['expect_body_contains']!r}")

    status = severity if problems else Status.PASS
    if not problems and probe.get("max_response_ms") and elapsed > int(probe["max_response_ms"]):
        status = Status.WARNING
        problems.append(f"slow: {elapsed} ms > {probe['max_response_ms']} ms")
    details = [f"{method} {url}", f"response: HTTP {response.status_code} {body_snippet}"]
    if probe.get("safe_because"):
        details.append(f"no delivery because: {probe['safe_because']}")
    message = "; ".join(problems) if problems else f"HTTP {response.status_code} as expected ({elapsed} ms)"
    if probe.get("description"):
        message = f"{probe['description']}: {message}"
    return CheckResult("form_endpoint", target, status, message, details=details,
                       metrics={"status_code": response.status_code, "response_ms": elapsed})


def run(ctx, options):
    severity = parse_severity(options.get("severity"), Status.FAIL)
    endpoint = options.get("url", "/api/send.php")
    probes = options.get("probes", [])
    if not probes:
        return [CheckResult("form_endpoint", endpoint, Status.SKIP, "No probes configured")]
    return [_run_probe(ctx, endpoint, probe, severity) for probe in probes]
