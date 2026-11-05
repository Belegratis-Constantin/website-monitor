"""Loading, defaulting and validating site profiles (``config/sites/*.yaml``)."""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("MONITOR_CONFIG_DIR", PROJECT_ROOT / "config" / "sites"))
# Git-ignored profiles that must not be published (real sites, internal
# endpoints, backend details). Searched before CONFIG_DIR.
PRIVATE_CONFIG_DIR = Path(os.environ.get("MONITOR_PRIVATE_CONFIG_DIR", PROJECT_ROOT / "config" / "private"))


def _profile_dirs() -> list[Path]:
    return [PRIVATE_CONFIG_DIR, CONFIG_DIR]

DEFAULTS: dict[str, Any] = {
    "timeout_seconds": 20,
    "user_agent": "WebsiteMonitor/2.0 (+https://github.com/)",
    # Pause between HTTP requests. Many shared hosts (fail2ban, mod_evasive)
    # ban IPs that fire bursts of requests; a ban looks exactly like an outage.
    "request_delay_ms": 300,
    "retries": 2,
    "paths": ["/"],
    "viewports": {
        "desktop": {"width": 1440, "height": 1000},
        "mobile": {"width": 390, "height": 844, "is_mobile": True, "has_touch": True},
    },
    "safety": {"block_urls": []},
    "suites": {},
    "checks": {},
}

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(ValueError):
    """Raised for a profile that cannot be used."""


def _substitute_env(value: Any, missing: list[str]) -> Any:
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            missing.append(name)
            return match.group(0)
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, list):
        return [_substitute_env(v, missing) for v in value]
    if isinstance(value, dict):
        return {k: _substitute_env(v, missing) for k, v in value.items()}
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def resolve_profile_path(name_or_path: str | os.PathLike[str]) -> Path:
    candidate = Path(name_or_path)
    if candidate.suffix in (".yaml", ".yml") and candidate.exists():
        return candidate.resolve()
    for directory in _profile_dirs():
        for suffix in (".yaml", ".yml"):
            path = directory / f"{name_or_path}{suffix}"
            if path.exists():
                return path
    available = ", ".join(list_sites()) or "none"
    raise ConfigError(f"Site profile '{name_or_path}' not found (available: {available})")


def list_sites() -> list[str]:
    names = set()
    for directory in _profile_dirs():
        if directory.exists():
            names |= {p.stem for p in directory.iterdir() if p.suffix in (".yaml", ".yml")}
    return sorted(names)


def load_site(name_or_path: str | os.PathLike[str], *, base_url: str | None = None) -> dict[str, Any]:
    """Load a profile, apply defaults and env substitution, and validate it.

    ``base_url`` overrides the profile's URL, e.g. to run the production
    profile against a staging deployment.
    """
    path = resolve_profile_path(name_or_path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")

    missing: list[str] = []
    data = _substitute_env(raw, missing)
    if base_url:
        data["base_url"] = base_url
    elif missing and "${" in str(data.get("base_url", "")):
        raise ConfigError(f"{path}: environment variable(s) not set: {', '.join(sorted(set(missing)))}")

    site = _deep_merge(DEFAULTS, data)
    site["_profile"] = path.stem
    site["_path"] = str(path)
    site["base_url"] = str(site.get("base_url", "")).rstrip("/")
    site.setdefault("name", path.stem)
    validate_site(site, source=str(path))
    return site


def validate_site(site: dict[str, Any], *, source: str = "profile") -> None:
    from monitor.checks import CHECKS  # local import: avoid cycle

    errors: list[str] = []
    parsed = urlparse(site.get("base_url", ""))
    if parsed.scheme not in ("http", "https") or not parsed.netloc or "REPLACE-WITH" in site["base_url"]:
        errors.append(f"base_url must be an absolute http(s) URL, got {site.get('base_url')!r}")

    paths = site.get("paths")
    if not isinstance(paths, list) or not paths:
        errors.append("paths must be a non-empty list")
    else:
        errors += [f"path {p!r} must start with '/'" for p in paths if not str(p).startswith("/")]

    for key in ("timeout_seconds", "request_delay_ms", "retries"):
        if not isinstance(site.get(key), (int, float)) or site[key] < 0:
            errors.append(f"{key} must be a non-negative number")

    viewports = site.get("viewports")
    if not isinstance(viewports, dict):
        errors.append("viewports must be a mapping of name -> {width, height}")
    else:
        for name, vp in viewports.items():
            if not isinstance(vp, dict) or not {"width", "height"} <= set(vp):
                errors.append(f"viewport '{name}' needs width and height")

    checks = site.get("checks")
    if not isinstance(checks, dict):
        errors.append("checks must be a mapping")
        checks = {}
    for name, options in checks.items():
        if name not in CHECKS:
            errors.append(f"unknown check '{name}' (available: {', '.join(sorted(CHECKS))})")
            continue
        if not isinstance(options, dict):
            errors.append(f"checks.{name} must be a mapping")
            continue
        if "enabled" in options and not isinstance(options["enabled"], bool):
            errors.append(f"checks.{name}.enabled must be true/false")
        if options.get("severity") not in (None, "warning", "fail"):
            errors.append(f"checks.{name}.severity must be 'warning' or 'fail'")

    for suite, members in (site.get("suites") or {}).items():
        if not isinstance(members, list):
            errors.append(f"suite '{suite}' must be a list of check names")
            continue
        errors += [f"suite '{suite}' references unknown check '{m}'" for m in members if m not in CHECKS]

    errors += _validate_form_endpoint(checks.get("form_endpoint") or {})
    errors += _validate_forms(checks.get("forms") or {})

    if errors:
        raise ConfigError(f"{source}:\n  - " + "\n  - ".join(errors))


def _validate_form_endpoint(options: dict[str, Any]) -> list[str]:
    errors = []
    for i, probe in enumerate(options.get("probes", []) or []):
        label = probe.get("name", f"#{i + 1}") if isinstance(probe, dict) else f"#{i + 1}"
        if not isinstance(probe, dict):
            errors.append(f"form_endpoint probe {label} must be a mapping")
            continue
        method = str(probe.get("method", "POST")).upper()
        # A POST probe hits the live mailer. Force the author to state why it
        # cannot deliver a message (honeypot, failed validation, ...).
        if method not in ("GET", "HEAD", "OPTIONS") and not str(probe.get("safe_because", "")).strip():
            errors.append(f"form_endpoint probe '{label}' sends {method}: add 'safe_because' explaining "
                          "why the server cannot deliver a message for it")
        if "expect_status" not in probe:
            errors.append(f"form_endpoint probe '{label}' needs expect_status")
    return errors


def _validate_forms(options: dict[str, Any]) -> list[str]:
    from monitor.checks.forms import SCENARIOS

    errors = []
    for i, form in enumerate(options.get("forms", []) or []):
        label = form.get("name", f"#{i + 1}") if isinstance(form, dict) else f"#{i + 1}"
        if not isinstance(form, dict):
            errors.append(f"forms entry {label} must be a mapping")
            continue
        for key in ("path", "form", "endpoint"):
            if not form.get(key):
                errors.append(f"form '{label}' needs '{key}'")
        unknown = set(form.get("scenarios", [])) - set(SCENARIOS)
        if unknown:
            errors.append(f"form '{label}' has unknown scenarios {sorted(unknown)} (available: {', '.join(SCENARIOS)})")
        if "live_honeypot" in form.get("scenarios", []) and not form.get("honeypot"):
            errors.append(f"form '{label}' uses live_honeypot but defines no 'honeypot' selector")
    return errors


def enabled_checks(site: dict[str, Any]) -> list[str]:
    return [name for name, opts in site.get("checks", {}).items() if opts.get("enabled", True)]
