"""Browser checks against the fixture site (skipped without Chromium)."""
from __future__ import annotations

import pytest

from monitor.models import Status

pytestmark = pytest.mark.browser


def by_target(results):
    return {r.target: r for r in results}


def test_browser_smoke_passes_on_clean_pages(browser_run):
    results = browser_run("browser")
    assert len(results) == 3
    assert all(r.status == Status.PASS for r in results), [(r.target, r.details) for r in results]


def test_browser_smoke_catches_broken_page(browser_run):
    (result,) = browser_run("browser", paths=["/broken.html"], screenshots="on_failure")
    text = "\n".join(result.details)
    assert result.status == Status.FAIL
    assert "uncaught JS error" in text
    assert "'undefined'" in text
    assert "HTTP 200" not in text  # soft 404 images are reported as broken images instead
    assert "horizontal overflow" in text
    assert "#nowhere" in text
    assert result.artifacts and result.artifacts[0].endswith(".png")


def test_browser_blocks_form_endpoint(browser_run, site, server):
    site["browser_init_script"] = ("window.addEventListener('load', () => "
                                   "fetch('/api/send.php', {method: 'POST'}).catch(() => {}))")
    (result,) = browser_run("browser", paths=["/"])
    assert result.metrics["blocked_requests"] == 1
    assert result.status == Status.PASS, result.details
    assert server.mailer_calls == []


def test_all_form_scenarios_pass_without_delivering(browser_run, server):
    results = by_target(browser_run("forms"))
    assert set(results) == {f"contact [{s}]" for s in
                            ("structure", "validation", "mock_success", "mock_error", "mock_network_error",
                             "live_honeypot")}
    assert all(r.status == Status.PASS for r in results.values()), \
        {t: r.details for t, r in results.items() if r.status != Status.PASS}
    # Only the live honeypot scenario reached the backend — and it stopped at the honeypot.
    assert server.mailer_calls == [(200, "honeypot")]
    assert server.deliveries == []


def test_live_honeypot_survives_removed_honeypot_guard(browser_run, server, monkeypatch):
    """Defence in depth: even if the server stopped checking the honeypot, the
    rewritten payload (fresh ts, unknown form type) is rejected — no delivery."""
    from tests import fixture_server

    original = fixture_server.Handler.do_POST

    def without_honeypot_guard(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).replace(b"website=", b"ignored=")
        self.headers["Content-Length"] = str(len(body))
        import io
        self.rfile = io.BytesIO(body)
        return original(self)

    monkeypatch.setattr(fixture_server.Handler, "do_POST", without_honeypot_guard)
    server.min_fill_seconds = 0  # and the time-trap is broken too
    try:
        results = by_target(browser_run("forms", forms=[{**_contact(browser_run), "scenarios": ["live_honeypot"]}]))
    finally:
        server.min_fill_seconds = 3
    assert results["contact [live_honeypot]"].status == Status.FAIL  # the regression is reported…
    assert server.deliveries == []  # …and still nothing was delivered
    assert server.mailer_calls == [(422, "form_type")]


def _contact(browser_run):
    from monitor.config import load_site
    from tests.conftest import FIXTURE_PROFILE
    return load_site(FIXTURE_PROFILE)["checks"]["forms"]["forms"][0]


def test_mock_success_detects_wrong_success_ui(browser_run):
    form = {**_contact(browser_run), "scenarios": ["mock_success"],
            "success": [{"selector": "#success", "text": "Welcome aboard"}]}
    (result,) = browser_run("forms", forms=[form])
    assert result.status == Status.FAIL and "Welcome aboard" in result.message


def test_mock_success_detects_payload_mismatch(browser_run):
    form = {**_contact(browser_run), "scenarios": ["mock_success"], "expect_payload": {"form_type": "newsletter"}}
    (result,) = browser_run("forms", forms=[form])
    assert result.status == Status.FAIL and "form_type" in result.message


def test_structure_detects_missing_honeypot(browser_run):
    form = {**_contact(browser_run), "scenarios": ["structure"], "honeypot": "#no-such-field"}
    (result,) = browser_run("forms", forms=[form])
    assert result.status == Status.FAIL and "honeypot" in result.message


def test_validation_detects_missing_client_validation(browser_run, site):
    # Remove the browser's validation by stripping `required` before the user submits.
    site["browser_init_script"] = ("document.addEventListener('DOMContentLoaded', () => "
                                   "document.querySelectorAll('[required]').forEach(e => e.required = false))")
    form = {**_contact(browser_run), "scenarios": ["validation"], "required_fields": [], "invalid_values": []}
    (result,) = browser_run("forms", forms=[form])
    assert result.status == Status.FAIL and "request was sent" in result.message


def test_interactions(browser_run):
    results = browser_run("interactions")
    assert all(r.status == Status.PASS for r in results), [r.message for r in results]


def test_interaction_failure_and_skip(browser_run):
    results = browser_run("interactions", interactions=[
        {"name": "fails", "path": "/", "steps": [{"expect_visible": "#nav"}]},
        {"name": "gated", "path": "/", "skip_unless_visible": ".lang-toggle", "steps": [{"click": ".lang-toggle"}]},
    ], step_timeout_ms=500)
    assert [r.status for r in results] == [Status.FAIL, Status.SKIP]
    assert "step 1 (expect_visible)" in results[0].message


def test_axe_runs(browser_run):
    (result,) = browser_run("axe")
    assert result.status in (Status.PASS, Status.WARNING), result.message
    assert "passes" in result.metrics
