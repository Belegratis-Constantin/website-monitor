"""HTTP-only checks against the local fixture server."""
from __future__ import annotations

import time

import requests

from monitor.http import HostUnreachable, HttpClient
from monitor.models import Status


def by_target(results):
    return {r.target: r for r in results}


def test_fake_mailer_really_records_deliveries(server):
    """Sanity check for the safety assertions below: a valid submission IS counted."""
    r = requests.post(f"{server.base_url}/api/send.php", headers={"Origin": server.base_url},
                      data={"form_type": "contact", "name": "A", "email": "a@example.invalid",
                            "ts": str(int((time.time() - 10) * 1000))}, timeout=5)
    assert r.json() == {"ok": True, "message": "ok"}
    assert len(server.deliveries) == 1


def test_uptime(run):
    results = run("uptime")
    assert [r.status for r in results] == [Status.PASS] * 3


def test_uptime_detects_unexpected_status(run):
    results = run("uptime", paths=["/"], expected_status=201)
    assert results[0].status == Status.FAIL


def test_form_endpoint_probes_never_deliver(run, server):
    results = run("form_endpoint")
    assert all(r.status == Status.PASS for r in results), [r.message for r in results]
    assert server.deliveries == []
    assert [g for _, g in server.mailer_calls] == ["method", "origin", "honeypot", "time-trap"]


def test_form_endpoint_detects_regression(run, server):
    server.min_fill_seconds = 0  # someone "fixed" the time-trap
    try:
        results = by_target(run("form_endpoint"))
    finally:
        server.min_fill_seconds = 3
    assert results["/api/send.php [time-trap]"].status == Status.FAIL
    assert server.deliveries == []  # unknown form type still stops it


def test_links_clean_site_passes(run):
    (result,) = run("links")
    assert result.status == Status.PASS, result.details


def test_links_finds_soft_404_and_missing_anchor(site, run):
    site["paths"] = ["/broken.html"]
    (result,) = run("links")
    text = "\n".join(result.details)
    assert result.status == Status.WARNING
    assert "/does-not-exist.html" in text and "soft 404" in text
    assert "#nowhere" in text


def test_assets(run):
    (result,) = run("assets")
    assert result.status == Status.PASS, result.details


def test_redirect_rules(run):
    results = run("redirects")
    assert all(r.status == Status.PASS for r in results), [r.message for r in results]


def test_exposure(run):
    (result,) = run("exposure")
    assert result.status == Status.PASS, result.details


def test_exposure_detects_leak(run):
    (result,) = run("exposure", paths=["/robots.txt"], use_defaults=False)
    assert result.status == Status.FAIL


def test_seo_reports_soft_404(run):
    results = run("seo")
    site_wide = [r for r in results if "site-wide" in r.target]
    assert site_wide and any("soft 404" in d for d in site_wide[0].details)
    pages = [r for r in results if r.target.endswith(".html")]
    assert pages and all(r.status == Status.PASS for r in pages), [r.details for r in pages]


def test_content(run):
    results = run("content", required={"/": ["Fixture Club", "Not on the page"]})
    home = next(r for r in results if r.target.endswith("/"))
    assert home.status == Status.WARNING and "Not on the page" in home.message


def test_accessibility(run):
    results = run("accessibility")
    assert all(r.status == Status.PASS for r in results), [r.details for r in results]


def test_headers_flags_missing_security_headers(run):
    (result,) = run("headers")
    assert result.status == Status.WARNING
    assert any("Content-Security-Policy" in d for d in result.details)


def test_http_client_cache_and_soft404(site, server):
    client = HttpClient(site)
    client.get(f"{server.base_url}/about.html")
    client.get(f"{server.base_url}/about.html")
    assert client.requests_made == 1
    response = client.get(f"{server.base_url}/nope.html")
    assert client.is_soft404(f"{server.base_url}/nope.html", response)
    assert not client.is_soft404(f"{server.base_url}/", client.get(f"{server.base_url}/"))


def test_circuit_breaker_stops_hammering_unreachable_host(site):
    site = {**site, "retries": 0, "connect_timeout_seconds": 1, "circuit_breaker_failures": 2}
    client = HttpClient(site)
    for _ in range(2):
        try:
            client.get("http://127.0.0.1:9/", cached=False)
        except requests.ConnectionError:
            pass
    started = time.perf_counter()
    try:
        client.get("http://127.0.0.1:9/x", cached=False)
        raise AssertionError("expected HostUnreachable")
    except HostUnreachable:
        pass
    assert time.perf_counter() - started < 0.1


def test_seo_noindex_pages_need_no_snippet_og_or_sitemap(site, run):
    site["paths"] = ["/", "/legal.html"]
    results = {r.target: r for r in run("seo", noindex_allowed=["/legal.html"])}
    legal = next(r for t, r in results.items() if t.endswith("/legal.html"))
    assert legal.status == Status.PASS, legal.details
    sitemap = next(r for t, r in results.items() if t.endswith("sitemap.xml"))
    assert not any("legal.html" in d for d in sitemap.details)
    flagged = {r.target: r for r in run("seo", noindex_allowed=[])}
    assert any("noindex" in d for r in flagged.values() if r.target.endswith("/legal.html") for d in r.details)


def test_browser_checks_not_run_after_host_became_unreachable(site, tmp_path):
    from monitor.runner import run_checks
    site = {**site, "base_url": "http://127.0.0.1:9", "connect_timeout_seconds": 1, "circuit_breaker_failures": 1}
    report = run_checks(site, ["uptime", "browser", "forms"], tmp_path)
    by_check = {r.check: r for r in report.results if r.check != "uptime"}
    assert set(by_check) == {"browser", "forms"}
    assert all(r.status == Status.FAIL and "stopped accepting connections" in r.message for r in by_check.values())
