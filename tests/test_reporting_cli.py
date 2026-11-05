from __future__ import annotations

import json
from xml.etree import ElementTree as ET

from monitor.cli import main
from monitor.models import CheckResult, RunReport, Status
from monitor.reporting import write_reports


def sample_report() -> RunReport:
    return RunReport(
        site={"name": "Demo <site>", "base_url": "https://example.com", "_profile": "demo"},
        results=[
            CheckResult("uptime", "/", Status.PASS, "HTTP 200", duration_ms=12),
            CheckResult("links", "/a|b", Status.WARNING, "1 broken link", details=["/x → 404"]),
            CheckResult("forms", "contact [mock_success]", Status.FAIL, "success UI missing <script>",
                        details=["payload keys: a"], artifacts=["screenshots/x.png"]),
            CheckResult("axe", "/", Status.SKIP, "axe-core not available"),
            CheckResult("ssl", "example.com", Status.ERROR, "crashed"),
        ],
        started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:05+00:00", duration_ms=5000,
        http_requests=7, checks_run=["uptime", "links", "forms", "axe", "ssl"])


def test_write_all_formats(tmp_path):
    out = tmp_path / "demo" / "run1"
    written = write_reports(sample_report(), out)
    assert set(written) == {"json", "md", "html", "junit"}

    data = json.loads(written["json"].read_text())
    assert data["status"] == "fail" and data["counts"]["warning"] == 1
    assert (out.parent / "latest.json").read_text() == written["json"].read_text()

    html = written["html"].read_text()
    assert "&lt;script&gt;" in html and "<script>success" not in html
    assert 'src="screenshots/x.png"' in html

    md = written["md"].read_text()
    assert "## Problems" in md and "/a\\|b" in md

    suites = {s.get("name"): s for s in ET.fromstring(written["junit"].read_text())}
    assert suites["forms"].get("failures") == "1"
    assert suites["ssl"].get("errors") == "1"
    assert suites["axe"].get("skipped") == "1"


def test_cli_validate_and_listing(capsys):
    assert main(["validate", "example"]) == 0
    assert "example" in capsys.readouterr().out
    assert main(["list-checks", "--site", "example"]) == 0
    out = capsys.readouterr().out
    assert "● browser" in out and "○ forms" in out and "[browser]" in out
    assert main(["list-sites"]) == 0


def test_cli_run_against_fixture(server, tmp_path, capsys, monkeypatch):
    from tests.conftest import FIXTURE_PROFILE
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = main(["run", "--site", str(FIXTURE_PROFILE), "--suite", "quick", "--output", str(tmp_path), "-v"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "PASS" in out
    assert (tmp_path / "fixture" / "latest.json").exists()
    assert "Fixture Club" in summary.read_text()
    assert server.deliveries == []


def test_cli_fail_on_warning(server, tmp_path):
    from tests.conftest import FIXTURE_PROFILE
    args = ["run", "--site", str(FIXTURE_PROFILE), "--checks", "headers", "--output", str(tmp_path), "-f", "json"]
    assert main(args) == 0
    assert main([*args, "--fail-on", "warning"]) == 1


def test_cli_config_error_exit_code(capsys):
    assert main(["run", "--site", "does-not-exist"]) == 2
    assert "not found" in capsys.readouterr().err


def test_cli_redact_hides_site_and_findings(server, tmp_path, capsys, monkeypatch):
    from tests.conftest import FIXTURE_PROFILE
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = main(["run", "--site", str(FIXTURE_PROFILE), "--checks", "uptime,headers", "--output", str(tmp_path),
                 "--redact", "-v"])
    out = capsys.readouterr().out + summary.read_text()
    assert code == 0
    assert "WARNING" in out  # the verdict is still visible
    for secret in (server.base_url, "127.0.0.1", "Fixture Club", "Content-Security-Policy", str(tmp_path)):
        assert secret not in out, secret
    assert "Content-Security-Policy" in next(tmp_path.glob("fixture/*/report.json")).read_text()


def test_cli_redact_hides_config_errors(capsys):
    assert main(["run", "--site", "does-not-exist", "--redact"]) == 2
    assert "does-not-exist" not in capsys.readouterr().err
