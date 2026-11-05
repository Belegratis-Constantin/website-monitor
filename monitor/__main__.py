import argparse
from pathlib import Path
from monitor.config import load_site
from monitor.runner import run_checks
from monitor.reporting import write_reports
from monitor.checks import available_checks

def main():
    parser = argparse.ArgumentParser(description="Reusable website monitoring")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Run enabled checks for a website")
    run_parser.add_argument("--site", required=True, help="Profile name in config/sites")
    run_parser.add_argument("--checks", help="Comma-separated check names")

    sub.add_parser("list-checks", help="List available check modules")
    args = parser.parse_args()

    if args.command == "list-checks":
        print("\n".join(available_checks()))
        return

    site = load_site(args.site)
    selected = [x.strip() for x in args.checks.split(",")] if args.checks else None
    results = run_checks(site, selected)
    report_dir = write_reports(site, results)
    print(f"Report: {report_dir}")
    failures = [r for r in results if r["status"] == "fail"]
    warnings = [r for r in results if r["status"] == "warning"]
    print(f"Checks: {len(results)} | Failures: {len(failures)} | Warnings: {len(warnings)}")
    raise SystemExit(1 if failures else 0)

if __name__ == "__main__":
    main()
