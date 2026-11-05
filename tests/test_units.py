"""Pure functions: no network, no browser."""
from __future__ import annotations

import json
import time
from urllib.parse import parse_qs

from monitor.checks.form_endpoint import expand, json_subset_mismatches
from monitor.checks.forms import HONEYPOT_VALUE, _Recorder, parse_payload
from monitor.models import CheckResult, RunReport, Status, issues_result, worst


def test_status_ordering():
    assert worst() == Status.PASS
    assert worst(Status.SKIP, Status.WARNING) == Status.WARNING
    assert worst(Status.WARNING, Status.ERROR, Status.PASS).rank == 2
    report = RunReport({"name": "x"}, [CheckResult("a", "t", Status.WARNING, "m")], "", "", 0)
    assert report.exit_code("fail") == 0 and report.exit_code("warning") == 1


def test_issues_result():
    assert issues_result("c", "t", [], "fine").status == Status.PASS
    r = issues_result("c", "t", list("abcde"), "fine", severity=Status.FAIL)
    assert r.status == Status.FAIL and "+2 more" in r.message and r.details == list("abcde")


def test_expand_placeholders():
    out = expand({"o": "{origin}", "h": "{host}", "t": "{now_ms}", "old": "{now_ms:-60}", "n": 5},
                 "https://example.com/sub")
    assert out["o"] == "https://example.com" and out["h"] == "example.com" and out["n"] == 5
    now = time.time() * 1000
    assert abs(int(out["t"]) - now) < 5000
    assert abs(int(out["old"]) - (now - 60000)) < 5000


def test_json_subset():
    assert json_subset_mismatches({"ok": True}, {"ok": True, "x": 1}) == []
    assert json_subset_mismatches({"a": {"b": 1}}, {"a": {"b": 2}}) == ["$.a.b: expected 1, got 2"]
    assert json_subset_mismatches({"a": 1}, {}) == ["$.a: missing"]


def test_parse_payload_formats():
    assert parse_payload("application/x-www-form-urlencoded", b"a=1&b=&c=x+y") == {"a": "1", "b": "", "c": "x y"}
    assert parse_payload("application/json", json.dumps({"a": 1}).encode()) == {"a": 1}
    body = (b"--XX\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\nAda\r\n"
            b"--XX\r\nContent-Disposition: form-data; name=\"website\"\r\n\r\n\r\n--XX--\r\n")
    assert parse_payload("multipart/form-data; boundary=XX", body) == {"name": "Ada", "website": ""}
    assert parse_payload("text/plain", None) == {}


class FakeRoute:
    def __init__(self, body: bytes, method="POST", ctype="application/x-www-form-urlencoded"):
        self.request = type("R", (), {"method": method, "url": "https://x/api/send.php",
                                      "headers": {"content-type": ctype}, "post_data_buffer": body})()
        self.calls = []

    def continue_(self, **kw):
        self.calls.append(("continue", kw))

    def abort(self, reason=None):
        self.calls.append(("abort", reason))

    def fulfill(self, **kw):
        self.calls.append(("fulfill", kw))


def test_live_recorder_refuses_requests_without_honeypot():
    """The single most important safety property: no honeypot → nothing reaches the mailer."""
    rec = _Recorder("live_if_honeypot", honeypot_name="website")
    route = FakeRoute(b"name=Ada&email=a%40b.c&website=")
    rec(route)
    assert route.calls == [("abort", "blockedbyclient")] and rec.refused

    route = FakeRoute(b"name=Ada&email=a%40b.c", method="POST")
    rec(route)
    assert route.calls[0][0] == "abort"

    route = FakeRoute(f"website={HONEYPOT_VALUE}".encode(), method="GET")
    rec(route)
    assert route.calls[0][0] == "abort"


def test_live_recorder_rewrites_payload_with_overrides():
    rec = _Recorder("live_if_honeypot", honeypot_name="website")
    rec.overrides = {"form_type": "probe", "ts": "123"}
    route = FakeRoute(f"form_type=membership&name=Ada&website={HONEYPOT_VALUE}".encode())
    rec(route)
    kind, kwargs = route.calls[0]
    assert kind == "continue"
    sent = {k: v[0] for k, v in parse_qs(kwargs["post_data"]).items()}
    assert sent == {"form_type": "probe", "ts": "123", "name": "Ada", "website": HONEYPOT_VALUE}


def test_live_recorder_refuses_overrides_on_non_urlencoded_body():
    rec = _Recorder("live_if_honeypot", honeypot_name="website")
    rec.overrides = {"form_type": "probe"}
    route = FakeRoute(json.dumps({"website": "x"}).encode(), ctype="application/json")
    rec(route)
    assert route.calls[0][0] == "abort"


def test_mock_and_block_modes_never_continue():
    for mode in ("fulfil", "network_error", "block"):
        route = FakeRoute(b"website=x")
        _Recorder(mode, {"status": 200, "json": {"ok": True}})(route)
        assert route.calls[0][0] in ("fulfill", "abort"), mode
