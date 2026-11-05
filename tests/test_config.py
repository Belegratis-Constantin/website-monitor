from __future__ import annotations

import copy

import pytest

from monitor.config import ConfigError, list_sites, load_site, validate_site
from monitor.runner import resolve_selection


def test_bundled_profiles_are_valid():
    assert "example" in list_sites()
    for name in list_sites():
        load_site(name)


def test_private_profiles_are_found_first(tmp_path, monkeypatch):
    import monitor.config as config
    (tmp_path / "example.yaml").write_text("name: Private copy\nbase_url: https://private.example\n")
    (tmp_path / "secret-site.yaml").write_text("base_url: https://secret.example\n")
    monkeypatch.setattr(config, "PRIVATE_CONFIG_DIR", tmp_path)
    assert load_site("example")["name"] == "Private copy"
    assert "secret-site" in list_sites()


def test_private_profiles_are_git_ignored():
    import subprocess
    out = subprocess.run(["git", "check-ignore", "config/private/anything.yaml"], capture_output=True, text=True)
    assert out.returncode == 0, "config/private/*.yaml must be git-ignored"


def test_base_url_override_and_env(monkeypatch):
    monkeypatch.setenv("EXAMPLE_BASE_URL", "http://localhost:5173")
    assert load_site("example")["base_url"] == "http://localhost:5173"
    assert load_site("example", base_url="https://staging.example/")["base_url"] == "https://staging.example"


def test_missing_env_is_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("NOPE_URL", raising=False)
    profile = tmp_path / "p.yaml"
    profile.write_text("base_url: ${NOPE_URL}\n")
    with pytest.raises(ConfigError, match="NOPE_URL"):
        load_site(profile)


@pytest.fixture
def base():
    return copy.deepcopy(load_site("example"))


@pytest.mark.parametrize("mutate, message", [
    (lambda s: s["checks"].update(bogus={}), "unknown check 'bogus'"),
    (lambda s: s["checks"]["uptime"].update(enabled="yes"), "enabled must be true/false"),
    (lambda s: s["checks"]["uptime"].update(severity="loud"), "severity"),
    (lambda s: s.update(paths=["no-slash"]), "must start with '/'"),
    (lambda s: s.update(suites={"quick": ["uptime", "nope"]}), "unknown check 'nope'"),
    (lambda s: s["checks"].update(form_endpoint={"probes": [{"name": "p", "expect_status": 200}]}),
     "add 'safe_because'"),
    (lambda s: s["checks"].update(form_endpoint={"probes": [{"name": "p", "method": "GET"}]}),
     "needs expect_status"),
    (lambda s: s["checks"].update(forms={"forms": [{"name": "f", "path": "/", "form": "form"}]}),
     "needs 'endpoint'"),
    (lambda s: s["checks"].update(forms={"forms": [{"name": "f", "path": "/", "form": "f", "endpoint": "**/x",
                                                     "scenarios": ["send_for_real"]}]}), "unknown scenarios"),
    (lambda s: s["checks"].update(forms={"forms": [{"name": "f", "path": "/", "form": "f", "endpoint": "**/x",
                                                     "scenarios": ["live_honeypot"]}]}), "no 'honeypot'"),
])
def test_validation_errors(base, mutate, message):
    mutate(base)
    with pytest.raises(ConfigError, match=message):
        validate_site(base)


def test_resolve_selection(base):
    base["suites"] = {"quick": ["uptime", "ssl"]}
    assert resolve_selection(base, suite="quick") == ["uptime", "ssl"]
    assert resolve_selection(base, checks=["seo", "seo", "links"], exclude=["links"]) == ["seo"]
    base["checks"]["links"]["enabled"] = False
    assert "links" not in resolve_selection(base)
    with pytest.raises(ConfigError):
        resolve_selection(base, suite="missing")
