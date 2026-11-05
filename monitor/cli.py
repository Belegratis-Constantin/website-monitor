"""Command-line interface: ``python -m monitor <command>``."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from monitor import __version__
from monitor.checks import CHECKS
from monitor.config import ConfigError, enabled_checks, list_sites, load_site
from monitor.models import CheckResult, Status
from monitor.reporting import FORMATS, write_reports
from monitor.reporting.markdown import render_markdown
from monitor.runner import resolve_selection, run_checks

DEFAULT_OUTPUT = Path("reports")


def _csv(value: str | None) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()] if value else []


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "site"


def _print_result(result: CheckResult, verbose: bool) -> None:
    if result.status == Status.PASS and not verbose:
        return
    print(f"  {result.status.icon} {result.check:<14} {result.target}: {result.message}", flush=True)
    if verbose and result.status.rank > 0:
        for line in result.details[:10]:
            print(f"      · {line}")


def cmd_run(args) -> int:
    site = load_site(args.site, base_url=args.base_url)
    names = resolve_selection(site, checks=_csv(args.checks) or None, suite=args.suite, exclude=_csv(args.exclude))
    if not names:
        print("No checks selected (enable some in the profile or pass --checks).", file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.output) / _slug(site["_profile"]) / stamp
    if args.redact:
        # Public CI logs: no site, URLs, targets or findings — only the verdict.
        print(f"Running {len(names)} check(s) (output redacted)", flush=True)
        on_result = None
    else:
        print(f"{site['name']} — {site['base_url']}\nChecks: {', '.join(names)}", flush=True)
        on_result = lambda r: _print_result(r, args.verbose)  # noqa: E731
    report = run_checks(site, names, out_dir, headless=not args.headed, on_result=on_result)
    formats = _csv(args.format) or list(FORMATS)
    written = write_reports(report, out_dir, formats)

    counts = report.counts()
    summary = " · ".join(f"{k} {v}" for k, v in counts.items() if v)
    if args.redact:
        print(f"\n{report.status.icon} {report.status.value.upper()}  ({summary}; {report.duration_ms / 1000:.0f} s)")
    else:
        print(f"\n{report.status.icon} {report.status.value.upper()}  ({summary}; "
              f"{report.duration_ms / 1000:.1f} s, {report.http_requests} HTTP requests)")
        for fmt, path in written.items():
            print(f"  {fmt:<5} {path}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            if args.redact:
                fh.write(f"## {report.status.icon} {report.status.value.upper()}\n\n{summary}\n\n"
                         "Details are in the encrypted report artifact.\n")
            else:
                fh.write(render_markdown(report))
    return report.exit_code(args.fail_on)


def cmd_list_checks(args) -> int:
    site = load_site(args.site) if args.site else None
    enabled = set(enabled_checks(site)) if site else set()
    for name, info in CHECKS.items():
        mark = ("●" if name in enabled else "○") if site else " "
        tag = " [browser]" if info.browser else ""
        print(f"{mark} {name:<14}{info.description}{tag}")
    return 0


def cmd_list_sites(args) -> int:
    for name in list_sites():
        try:
            site = load_site(name)
            print(f"{name:<20} {site['base_url']:<35} {len(enabled_checks(site))} checks enabled")
        except ConfigError as exc:
            print(f"{name:<20} INVALID: {str(exc).splitlines()[0]}")
    return 0


def cmd_validate(args) -> int:
    names = args.sites or list_sites()
    bad = 0
    for name in names:
        try:
            site = load_site(name, base_url=args.base_url)
            suites = ", ".join(site.get("suites") or {}) or "none"
            print(f"✅ {name}: {len(enabled_checks(site))} checks enabled, suites: {suites}")
        except ConfigError as exc:
            bad += 1
            print(f"❌ {exc}")
    return 1 if bad else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="monitor", description="Configuration-driven website monitoring")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--debug", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run checks for a site profile")
    run.add_argument("--site", "-s", required=True, help="profile name in config/sites or path to a YAML file")
    run.add_argument("--checks", "-c", help="comma-separated checks (overrides suite/profile)")
    run.add_argument("--suite", help="named suite from the profile, e.g. quick or full")
    run.add_argument("--exclude", "-x", help="comma-separated checks to skip")
    run.add_argument("--base-url", help="run the profile against another deployment (e.g. staging)")
    run.add_argument("--output", "-o", default=str(DEFAULT_OUTPUT), help="report root directory (default: reports)")
    run.add_argument("--format", "-f", help=f"comma-separated report formats ({', '.join(FORMATS)}; default all)")
    run.add_argument("--fail-on", choices=["fail", "warning"], default="fail",
                     help="exit 1 on failures only (default) or on warnings too")
    run.add_argument("--headed", action="store_true", help="show the browser window")
    run.add_argument("--verbose", "-v", action="store_true", help="print passing results and details")
    run.add_argument("--redact", action="store_true", default=os.environ.get("MONITOR_REDACT") == "1",
                     help="print only the overall verdict and counts (for public CI logs; env MONITOR_REDACT=1)")
    run.set_defaults(func=cmd_run)

    lc = sub.add_parser("list-checks", help="list available checks")
    lc.add_argument("--site", "-s", help="mark the checks this profile enables")
    lc.set_defaults(func=cmd_list_checks)

    ls = sub.add_parser("list-sites", help="list site profiles")
    ls.set_defaults(func=cmd_list_sites)

    va = sub.add_parser("validate", help="validate site profiles without network access")
    va.add_argument("sites", nargs="*", help="profiles (default: all)")
    va.add_argument("--base-url", help="substitute base_url (for profiles using ${VAR})")
    va.set_defaults(func=cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if getattr(args, "redact", False):
        logging.getLogger().setLevel(logging.CRITICAL)  # log lines carry URLs
    elif not args.debug:
        logging.getLogger("urllib3").setLevel(logging.ERROR)  # retries are reported in results
    try:
        return args.func(args)
    except ConfigError as exc:
        if getattr(args, "redact", False):
            print("Configuration error (details redacted; run 'monitor validate' locally)", file=sys.stderr)
        else:
            print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
