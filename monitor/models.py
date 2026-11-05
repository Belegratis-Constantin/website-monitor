"""Result types shared by every check, the runner and the reporters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"  # the check itself crashed; treated like a failure

    @property
    def rank(self) -> int:
        return {"pass": 0, "skip": 0, "warning": 1, "fail": 2, "error": 2}[self.value]

    @property
    def icon(self) -> str:
        return {"pass": "✅", "skip": "⏭️", "warning": "⚠️", "fail": "❌", "error": "💥"}[self.value]


def worst(*statuses: Status) -> Status:
    """Most severe of the given statuses (PASS when none are given)."""
    result = Status.PASS
    for status in statuses:
        if status.rank > result.rank:
            result = status
    return result


@dataclass
class CheckResult:
    check: str
    target: str
    status: Status
    message: str
    details: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "check": self.check,
            "target": self.target,
            "status": self.status.value,
            "message": self.message,
        }
        if self.details:
            data["details"] = self.details
        if self.metrics:
            data["metrics"] = self.metrics
        if self.artifacts:
            data["artifacts"] = self.artifacts
        if self.duration_ms is not None:
            data["duration_ms"] = self.duration_ms
        return data


def issues_result(check: str, target: str, issues: list[str], ok_message: str, *,
                  severity: Status = Status.WARNING, summary: str | None = None,
                  metrics: dict[str, Any] | None = None, max_inline: int = 3) -> CheckResult:
    """Build a result from a list of issue strings: PASS when empty, else ``severity``."""
    if not issues:
        return CheckResult(check, target, Status.PASS, ok_message, metrics=metrics or {})
    head = summary or f"{len(issues)} issue(s): " + "; ".join(issues[:max_inline])
    if summary is None and len(issues) > max_inline:
        head += f"; … (+{len(issues) - max_inline} more)"
    return CheckResult(check, target, severity, head, details=issues, metrics=metrics or {})


def parse_severity(value: Any, default: Status = Status.WARNING) -> Status:
    """Translate the ``severity`` option used by quality checks into a Status."""
    if value is None:
        return default
    status = Status(str(value).lower())
    if status not in (Status.WARNING, Status.FAIL):
        raise ValueError(f"severity must be 'warning' or 'fail', not {value!r}")
    return status


@dataclass
class RunReport:
    site: dict[str, Any]
    results: list[CheckResult]
    started_at: str
    finished_at: str
    duration_ms: int
    http_requests: int = 0
    checks_run: list[str] = field(default_factory=list)

    @property
    def status(self) -> Status:
        return worst(*(r.status for r in self.results))

    def counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Status}
        for result in self.results:
            counts[result.status.value] += 1
        return counts

    def exit_code(self, fail_on: str = "fail") -> int:
        threshold = Status.WARNING.rank if fail_on == "warning" else Status.FAIL.rank
        return 1 if any(r.status.rank >= threshold for r in self.results) else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "site": self.site.get("name"),
            "profile": self.site.get("_profile"),
            "base_url": self.site.get("base_url"),
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "http_requests": self.http_requests,
            "checks_run": self.checks_run,
            "counts": self.counts(),
            "results": [r.to_dict() for r in self.results],
        }
