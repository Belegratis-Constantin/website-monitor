"""Chromium smoke test for every page × viewport.

Fails on: navigation errors, HTTP >= 400, uncaught JS exceptions, failed or
4xx/5xx same-origin requests, empty title/body, forbidden URLs (e.g. a
pre-launch/maintenance redirect) and forbidden rendered text such as
``undefined`` left behind by a broken template.
Warns on: console errors, broken images, horizontal overflow, missing
in-page anchor targets, budget overruns.

Options:
  paths:            default: profile paths
  viewports:        names from the profile's ``viewports`` (default: all)
  wait_until:       load | domcontentloaded | networkidle (default load)
  settle_ms:        extra wait for client-side rendering (default 500)
  console_errors:   warning | fail | ignore (default warning)
  ignore_console:   regexes of console messages to ignore
  ignore_requests:  regexes of request URLs whose failure is ignored
  forbid_url:       regexes the final URL must not match
  expect_text:      {path: [text, ...]} that must be visible after rendering
  forbidden_text:   texts that must not appear in the rendered body
  max_load_ms:      warn when the load event is slower (default 5000)
  max_transfer_kb:  warn when total transferred bytes exceed this (default 3000)
  screenshots:      on_failure | always | never (default on_failure)
"""

from __future__ import annotations

import re

from monitor.models import CheckResult, Status, worst

DEFAULT_FORBIDDEN_TEXT = ["undefined", "[object Object]", "NaN", "{{", "}}"]

METRICS_JS = """() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const res = performance.getEntriesByType('resource');
  const lcp = performance.getEntriesByType('largest-contentful-paint').slice(-1)[0];
  return {
    dom_content_loaded_ms: Math.round(nav.domContentLoadedEventEnd || 0),
    load_ms: Math.round(nav.loadEventEnd || 0),
    transfer_kb: Math.round(((nav.transferSize || 0) + res.reduce((s, r) => s + (r.transferSize || 0), 0)) / 1024),
    requests: res.length + 1,
    lcp_ms: lcp ? Math.round(lcp.startTime) : null,
  };
}"""

PAGE_FACTS_JS = """() => {
  const doc = document.documentElement;
  const brokenImages = [...document.images]
    .filter(img => img.complete && img.naturalWidth === 0 && img.currentSrc)
    .map(img => img.currentSrc);
  const ids = new Set([...document.querySelectorAll('[id]')].map(e => e.id));
  const names = new Set([...document.querySelectorAll('a[name]')].map(e => e.name));
  const missingAnchors = [...document.querySelectorAll('a[href^="#"]')]
    .map(a => decodeURIComponent(a.getAttribute('href').slice(1)))
    .filter(id => id && !ids.has(id) && !names.has(id));
  const wide = [...document.body.querySelectorAll('*')]
    .filter(el => el.getBoundingClientRect().right > doc.clientWidth + 2
                  && getComputedStyle(el).position !== 'fixed')
    .slice(0, 3)
    .map(el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
               (el.classList.length ? '.' + [...el.classList].join('.') : ''));
  return {
    overflow: doc.scrollWidth > doc.clientWidth + 2,
    overflowCulprits: wide,
    brokenImages: [...new Set(brokenImages)],
    missingAnchors: [...new Set(missingAnchors)],
    title: document.title.trim(),
    bodyText: document.body ? document.body.innerText : '',
  };
}"""


def _contains_token(text: str, token: str) -> bool:
    if re.fullmatch(r"\w+", token):
        return re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", text) is not None
    return token in text


def _check_page(ctx, options, path: str, viewport: str) -> CheckResult:
    url = ctx.url(path)
    target = f"{path} [{viewport}]"
    base = ctx.base_url
    ignore_console = [re.compile(p) for p in options.get("ignore_console", [])]
    ignore_requests = [re.compile(p) for p in options.get("ignore_requests", [])]
    forbid_url = [re.compile(p) for p in options.get("forbid_url", [])]
    console_mode = options.get("console_errors", "warning")

    failures: list[str] = []
    warnings: list[str] = []
    context, page = ctx.browser.new_page(viewport)
    blocked_before = len(ctx.browser.blocked_requests)

    def on_console(msg):
        if msg.type == "error" and not any(p.search(msg.text) for p in ignore_console):
            if "net::ERR_BLOCKED_BY_CLIENT" in msg.text or "Failed to load resource" in msg.text:
                return  # duplicates of request failures, reported separately
            (failures if console_mode == "fail" else warnings).append(f"console error: {msg.text[:200]}")

    def on_request_failed(req):
        if req.url.startswith(base) and not any(p.search(req.url) for p in ignore_requests):
            if req.failure and "ERR_BLOCKED_BY_CLIENT" in req.failure:
                return  # blocked on purpose by safety.block_urls
            failures.append(f"request failed: {req.url} ({req.failure})")

    def on_response(resp):
        if resp.url.startswith(base) and resp.status >= 400 and not any(p.search(resp.url) for p in ignore_requests):
            failures.append(f"HTTP {resp.status}: {resp.url}")

    page.on("pageerror", lambda err: failures.append(f"uncaught JS error: {str(err)[:300]}"))
    if console_mode != "ignore":
        page.on("console", on_console)
    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)

    metrics: dict = {}
    artifacts: list[str] = []
    try:
        response = page.goto(url, wait_until=options.get("wait_until", "load"),
                             timeout=int(options.get("navigation_timeout_ms", 30000)))
        if response is None or response.status >= 400:
            failures.append(f"navigation returned HTTP {response.status if response else 'none'}")
        try:
            page.wait_for_load_state("networkidle", timeout=int(options.get("networkidle_timeout_ms", 5000)))
        except Exception:
            warnings.append("network did not become idle within the timeout")
        page.wait_for_timeout(int(options.get("settle_ms", 500)))

        for pattern in forbid_url:
            if pattern.search(page.url):
                failures.append(f"ended on forbidden URL {page.url}")

        facts = page.evaluate(PAGE_FACTS_JS)
        metrics = page.evaluate(METRICS_JS)
        body = facts["bodyText"]
        if not facts["title"]:
            failures.append("document title is empty")
        if not body.strip():
            failures.append("rendered body is empty")
        for text in options.get("expect_text", {}).get(path, []) or []:
            if text.casefold() not in body.casefold():
                failures.append(f"expected text not rendered: {text!r}")
        for token in options.get("forbidden_text", DEFAULT_FORBIDDEN_TEXT):
            if _contains_token(body, token):
                failures.append(f"rendered page contains {token!r} (template/data bug?)")
        if facts["overflow"]:
            warnings.append("horizontal overflow; widest elements: " + ", ".join(facts["overflowCulprits"] or ["?"]))
        for src in facts["brokenImages"]:
            warnings.append(f"image failed to render: {src}")
        for anchor in facts["missingAnchors"]:
            warnings.append(f"in-page link #{anchor} has no target")
        if metrics.get("load_ms", 0) > int(options.get("max_load_ms", 5000)):
            warnings.append(f"load event after {metrics['load_ms']} ms")
        if metrics.get("transfer_kb", 0) > int(options.get("max_transfer_kb", 3000)):
            warnings.append(f"page transferred {metrics['transfer_kb']} KB")
        metrics["blocked_requests"] = len(ctx.browser.blocked_requests) - blocked_before
    except Exception as exc:
        failures.append(f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}")

    status = worst(Status.FAIL if failures else Status.PASS, Status.WARNING if warnings else Status.PASS)
    shots = options.get("screenshots", "on_failure")
    if shots == "always" or (shots == "on_failure" and status != Status.PASS):
        try:
            shot = ctx.artifact_path("screenshots", f"browser_{path}_{viewport}.png")
            page.screenshot(path=str(shot), full_page=True)
            artifacts.append(ctx.relative_artifact(shot))
        except Exception:
            pass
    context.close()

    issues = failures + warnings
    if issues:
        message = f"{len(failures)} failure(s), {len(warnings)} warning(s): " + "; ".join(issues[:2])
    else:
        message = (f"rendered OK — load {metrics.get('load_ms')} ms, {metrics.get('requests')} requests, "
                   f"{metrics.get('transfer_kb')} KB")
    return CheckResult("browser", target, status, message, details=issues, metrics=metrics, artifacts=artifacts)


def run(ctx, options):
    paths = options.get("paths", ctx.paths)
    viewports = options.get("viewports", list(ctx.site.get("viewports", {})))
    return [_check_page(ctx, options, path, vp) for path in paths for vp in viewports]
