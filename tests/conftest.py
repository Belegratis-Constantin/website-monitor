from __future__ import annotations

import os
from pathlib import Path

import pytest

from monitor.config import load_site
from monitor.runner import run_checks
from tests.fixture_server import FixtureServer

FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "sites" / "fixture.yaml"


@pytest.fixture(scope="session")
def server():
    srv = FixtureServer().start()
    os.environ["FIXTURE_BASE_URL"] = srv.base_url
    yield srv
    srv.stop()


@pytest.fixture(autouse=True)
def _reset_server(request):
    if "server" in request.fixturenames:
        srv = request.getfixturevalue("server")
        srv.deliveries.clear()
        srv.mailer_calls.clear()
        srv.mailer_status = 200
    yield


@pytest.fixture
def site(server):
    return load_site(FIXTURE_PROFILE)


@pytest.fixture
def run(site, tmp_path):
    """run("links", options_override={...}) -> list of results for that check."""
    def _run(check: str, **overrides):
        if overrides:
            site["checks"][check] = {**site["checks"].get(check, {}), **overrides}
        return run_checks(site, [check], tmp_path).results
    return _run


@pytest.fixture(scope="session")
def chromium_available():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


@pytest.fixture
def browser_run(run, chromium_available):
    if not chromium_available:
        pytest.skip("Playwright/Chromium not installed")
    return run
