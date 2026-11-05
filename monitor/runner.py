"""Runs the selected checks for one site and collects their results."""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monitor.checks import CHECKS, get_check
from monitor.config import ConfigError, enabled_checks
from monitor.context import BrowserUnavailable, RunContext
from monitor.models import CheckResult, RunReport, Status

log = logging.getLogger("monitor")


class HostUnreachableDuringRun(RuntimeError):
    pass


def resolve_selection(site: dict[str, Any], *, checks: Iterable[str] | None = None,
                      suite: str | None = None, exclude: Iterable[str] = ()) -> list[str]:
    """Which checks to run: explicit list > suite > every enabled check in the profile."""
    if checks:
        names = list(checks)
    elif suite:
        suites = site.get("suites") or {}
        if suite not in suites:
            raise ConfigError(f"suite '{suite}' not defined in profile (defined: {', '.join(suites) or 'none'})")
        names = list(suites[suite])
    else:
        names = enabled_checks(site)
    unknown = [n for n in names if n not in CHECKS]
    if unknown:
        raise ConfigError(f"unknown check(s): {', '.join(unknown)} (available: {', '.join(CHECKS)})")
    excluded = set(exclude)
    return [n for n in dict.fromkeys(names) if n not in excluded]


def run_checks(site: dict[str, Any], names: list[str], output_dir: Path, *, headless: bool = True,
               on_result: Callable[[CheckResult], None] | None = None) -> RunReport:
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    ctx = RunContext(site, output_dir, headless=headless)
    results: list[CheckResult] = []
    try:
        for name in names:
            options = dict(site.get("checks", {}).get(name) or {})
            check_started = time.perf_counter()
            log.info("running %s", name)
            try:
                if CHECKS[name].browser and ctx.http.circuit_open(site["base_url"]):
                    # The host stopped accepting connections earlier in this run (outage or
                    # IP ban); dozens of identical browser timeouts would only add noise.
                    raise HostUnreachableDuringRun(
                        f"not run: {site['base_url']} stopped accepting connections earlier in this run "
                        "(outage, or the monitor's IP was blocked)")
                produced = list(get_check(name).run(ctx, options))
            except HostUnreachableDuringRun as exc:
                produced = [CheckResult(name, site["base_url"], Status.FAIL, str(exc))]
            except BrowserUnavailable as exc:
                produced = [CheckResult(name, site["base_url"], Status.ERROR, str(exc))]
            except Exception as exc:  # a crashing check must not stop the run
                log.debug("check %s crashed", name, exc_info=True)
                produced = [CheckResult(name, site["base_url"], Status.ERROR,
                                        f"Check crashed: {type(exc).__name__}: {exc}",
                                        details=traceback.format_exc().strip().splitlines()[-6:])]
            elapsed = round((time.perf_counter() - check_started) * 1000)
            if not produced:
                produced = [CheckResult(name, site["base_url"], Status.SKIP, "Nothing configured to check")]
            for result in produced:
                result.check = result.check or name
                if result.duration_ms is None and len(produced) == 1:
                    result.duration_ms = elapsed
                results.append(result)
                if on_result:
                    on_result(result)
    finally:
        requests_made = ctx.http.requests_made
        ctx.close()
    return RunReport(
        site=site,
        results=results,
        started_at=started.isoformat(timespec="seconds"),
        finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        duration_ms=round((time.perf_counter() - t0) * 1000),
        http_requests=requests_made,
        checks_run=names,
    )
