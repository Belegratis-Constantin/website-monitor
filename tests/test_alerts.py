from monitor.alerts import MARKER, body, decide, summarize, title


def report(*results, status="warning"):
    counts = {"pass": 0, "warning": 0, "fail": 0, "skip": 0, "error": 0}
    for r in results:
        counts[r[2]] += 1
    return {"status": status, "counts": counts,
            "results": [{"check": c, "target": t, "status": s, "message": f"took {i} ms"}
                        for i, (c, t, s) in enumerate(results)]}


def test_fingerprint_ignores_messages_and_passes():
    a = summarize(report(("headers", "/", "warning"), ("uptime", "/", "pass")))
    b = summarize(report(("uptime", "/", "pass"), ("headers", "/", "warning")))
    assert a.fingerprint == b.fingerprint
    c = summarize(report(("headers", "/", "fail"), status="fail"))
    assert c.fingerprint != a.fingerprint


def test_decide_lifecycle():
    warn = summarize(report(("headers", "/", "warning")))
    assert decide(warn, None) == "open"
    issue = {"number": 1, "body": body(warn, "full", "url")}
    assert decide(warn, issue) == "none"  # same problems → no repeated email
    worse = summarize(report(("headers", "/", "warning"), ("uptime", "/", "fail"), status="fail"))
    assert decide(worse, issue) == "update"
    ok = summarize(report(("uptime", "/", "pass"), status="pass"))
    assert decide(ok, issue) == "close"
    assert decide(ok, None) == "none"


def test_issue_text_is_public_safe():
    s = summarize(report(("forms", "membership [live_honeypot]", "fail"), status="fail"))
    text = title(s, "full") + body(s, "full", "https://github.com/x/y/actions/runs/1")
    assert "membership" not in text and "forms" not in text
    assert "1 fail" in text and MARKER.search(text).group(1) == s.fingerprint
