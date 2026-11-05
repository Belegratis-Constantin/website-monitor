"""Alerting through a GitHub issue — GitHub emails the repository owner.

One open issue per suite while the site has warnings or failures:

* new problems       → issue opened                (email)
* problems change    → comment + title updated     (email)
* same problems      → nothing                     (no repeated email every run)
* everything passes  → comment + issue closed      (email)

Issue text contains only the verdict and counts, never site details, so it is
safe in a public repository. "Same problems" is decided by a fingerprint over
(check, target, status) of all non-passing results; messages are ignored
because they contain timings that change on every run.

Usage (in GitHub Actions)::

    python -m monitor.alerts reports/site/latest.json --suite full

Needs GITHUB_TOKEN (issues: write), GITHUB_REPOSITORY, and optionally
GITHUB_SERVER_URL / GITHUB_RUN_ID for the run link.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

PROBLEM_STATUSES = {"warning": 1, "fail": 2, "error": 2}
ICONS = {"pass": "✅", "warning": "⚠️", "fail": "❌", "error": "💥", "skip": "⏭️"}
MARKER = re.compile(r"<!-- monitor-fingerprint: ([0-9a-f]+) -->")


@dataclass
class Summary:
    status: str
    counts: dict[str, int]
    fingerprint: str

    @property
    def has_problems(self) -> bool:
        return self.status in PROBLEM_STATUSES

    def counts_text(self) -> str:
        return ", ".join(f"{n} {k}" for k, n in self.counts.items() if n and k in PROBLEM_STATUSES) or "no problems"


def summarize(report: dict[str, Any]) -> Summary:
    problems = sorted(f"{r['check']}|{r['target']}|{r['status']}" for r in report.get("results", [])
                      if r.get("status") in PROBLEM_STATUSES)
    fingerprint = hashlib.sha256("\n".join(problems).encode()).hexdigest()[:16]
    return Summary(report.get("status", "error"), dict(report.get("counts", {})), fingerprint)


def decide(summary: Summary, open_issue: dict[str, Any] | None) -> str:
    """'open' | 'update' | 'close' | 'none'."""
    if not summary.has_problems:
        return "close" if open_issue else "none"
    if open_issue is None:
        return "open"
    match = MARKER.search(open_issue.get("body") or "")
    return "none" if match and match.group(1) == summary.fingerprint else "update"


def title(summary: Summary, suite: str) -> str:
    return f"{ICONS.get(summary.status, '')} Monitor ({suite}): {summary.status.upper()} — {summary.counts_text()}"


def body(summary: Summary, suite: str, run_url: str) -> str:
    return (f"The **{suite}** monitoring run reports **{summary.status.upper()}**: {summary.counts_text()}.\n\n"
            f"Latest run with a change: {run_url}\n\n"
            "Details are only in the encrypted report artifact of that run (this repository is public). "
            "Decrypt it as described in the README.\n\n"
            "This issue is updated when the set of problems changes and closed automatically once "
            "everything passes again.\n\n"
            f"<!-- monitor-fingerprint: {summary.fingerprint} -->\n")


class GitHub:
    def __init__(self, repo: str, token: str, api: str = "https://api.github.com"):
        self.base = f"{api}/repos/{repo}"
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}",
                                     "Accept": "application/vnd.github+json",
                                     "X-GitHub-Api-Version": "2022-11-28"})

    def _call(self, method: str, path: str, **kwargs) -> Any:
        r = self.session.request(method, self.base + path, timeout=20, **kwargs)
        if r.status_code == 422 and path == "/labels":
            return None  # label exists
        r.raise_for_status()
        return r.json() if r.content else None

    def ensure_label(self, name: str) -> None:
        self._call("POST", "/labels", json={"name": name, "color": "d93f0b",
                                            "description": "Opened by the scheduled website monitor"})

    def open_issue(self, label: str) -> dict[str, Any] | None:
        issues = self._call("GET", "/issues", params={"state": "open", "labels": label, "per_page": 5})
        issues = [i for i in issues if "pull_request" not in i]
        return issues[0] if issues else None

    def create(self, title: str, body: str, label: str) -> dict[str, Any]:
        return self._call("POST", "/issues", json={"title": title, "body": body, "labels": [label]})

    def comment(self, number: int, text: str) -> None:
        self._call("POST", f"/issues/{number}/comments", json={"body": text})

    def edit(self, number: int, **fields) -> None:
        self._call("PATCH", f"/issues/{number}", json=fields)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m monitor.alerts")
    parser.add_argument("report", type=Path, help="report.json / latest.json of the run")
    parser.add_argument("--suite", default="full")
    args = parser.parse_args(argv)

    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {"status": "error", "counts": {"error": 1}, "results": [
            {"check": "monitor", "target": "run", "status": "error"}]}
    summary = summarize(report)
    run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
               f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
    gh = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
    label = f"monitor-{args.suite}"
    gh.ensure_label(label)
    issue = gh.open_issue(label)
    action = decide(summary, issue)

    if action == "open":
        created = gh.create(title(summary, args.suite), body(summary, args.suite, run_url), label)
        print(f"opened issue #{created['number']}")
    elif action == "update":
        gh.comment(issue["number"], f"{ICONS.get(summary.status, '')} Problems changed: **{summary.status.upper()}** "
                                    f"— {summary.counts_text()}. Run: {run_url}")
        gh.edit(issue["number"], title=title(summary, args.suite), body=body(summary, args.suite, run_url))
        print(f"updated issue #{issue['number']}")
    elif action == "close":
        gh.comment(issue["number"], f"✅ All checks pass again. Run: {run_url}")
        gh.edit(issue["number"], state="closed", state_reason="completed")
        print(f"closed issue #{issue['number']}")
    else:
        print("no change; no notification")
    return 0


if __name__ == "__main__":
    sys.exit(main())
