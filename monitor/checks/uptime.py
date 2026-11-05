"""Is every configured path reachable, fast enough and not redirected away?

Options:
  expected_status:        int or list (default 200)
  max_response_time_ms:   warning threshold for time-to-first-byte (default 3000)
  fail_response_time_ms:  failure threshold (default: none)
  allow_redirects_to:     regexes a path may redirect to (default: none -> redirect = warning)
  forbid_final_url:       regexes that must never be the final URL (e.g. a maintenance/countdown page)
  paths:                  override the profile's paths
"""

from __future__ import annotations

import re

import requests

from monitor.http import describe_error
from monitor.models import CheckResult, Status, worst


def run(ctx, options):
    expected = options.get("expected_status", 200)
    expected = set(expected if isinstance(expected, list) else [expected])
    warn_ms = int(options.get("max_response_time_ms", 3000))
    fail_ms = options.get("fail_response_time_ms")
    allow = [re.compile(p) for p in options.get("allow_redirects_to", [])]
    forbid = [re.compile(p) for p in options.get("forbid_final_url", [])]

    results = []
    for path in options.get("paths", ctx.paths):
        url = ctx.url(path)
        try:
            response = ctx.http.get(url)
        except requests.RequestException as exc:
            results.append(CheckResult("uptime", url, Status.FAIL, describe_error(exc)))
            continue

        ttfb = round(response.elapsed.total_seconds() * 1000)
        notes, status = [], Status.PASS
        if response.status_code not in expected:
            status = Status.FAIL
            notes.append(f"expected HTTP {'/'.join(map(str, sorted(expected)))}")
        if response.history:
            hops = " → ".join(f"{r.status_code} {r.url}" for r in response.history)
            if any(p.search(response.url) for p in forbid):
                status = Status.FAIL
                notes.append(f"redirected to forbidden URL ({hops} → {response.url})")
            elif not any(p.search(response.url) for p in allow):
                status = worst(status, Status.WARNING)
                notes.append(f"redirected: {hops} → {response.url}")
        if fail_ms is not None and ttfb > int(fail_ms):
            status = Status.FAIL
            notes.append(f"slower than {fail_ms} ms fail threshold")
        elif ttfb > warn_ms:
            status = worst(status, Status.WARNING)
            notes.append(f"slower than {warn_ms} ms threshold")

        message = f"HTTP {response.status_code} in {ttfb} ms"
        if notes:
            message += "; " + "; ".join(notes)
        results.append(CheckResult("uptime", url, status, message,
                                   metrics={"status_code": response.status_code, "ttfb_ms": ttfb,
                                            "bytes": len(response.content)}))
    return results
