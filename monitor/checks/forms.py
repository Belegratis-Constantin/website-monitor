"""End-to-end form tests in Chromium that never deliver a real message.

Every scenario intercepts the form endpoint with a Playwright route *before*
the page loads, so the browser's request ends in the monitor, not in the
mailer:

  structure           fields, labels, submit button and honeypot are present;
                      reports (and skips the rest) when the form is paused
  validation          an empty / incomplete / invalid form sends no request
  mock_success        the payload the browser sends is checked, a fake
                      success is answered, and the success UI must appear
  mock_error          the backend answers an error and the error UI must appear
  mock_network_error  the request fails at network level; the error UI must appear
  live_honeypot       the *real* backend is called, but only after the route
                      has verified that the outgoing payload has the honeypot
                      filled. The server then pretends success and drops the
                      message. Any request without the honeypot is aborted.
                      ``live_payload_overrides`` (e.g. a fresh time-trap
                      timestamp and an unknown form type) are merged into the
                      body first, so a second server guard would still stop
                      delivery if the honeypot guard were ever removed.

Per-form options (``checks.forms.forms`` is a list of these):
  name, path, form (CSS selector), endpoint (URL glob, e.g. "**/api/send.php")
  fields:            {selector: value | {select_index: n} | {select_value: v}
                      | {select_label: t} | {check: true}}
  submit:            submit selector (default: "<form> [type=submit]")
  honeypot:          selector of the hidden spam-trap field
  required_fields:   selectors; validation leaves each one empty in turn
  invalid_values:    [{field: selector, value: text}] that must block sending
  expect_payload:    {name: value} subset the request body must contain
  expect_payload_keys: names that must be present (e.g. the time-trap "ts")
  expect_method:     default POST
  success:           expectation(s) after a successful submit
  error:             expectation(s) after a failed submit
                     (an expectation is {selector, state: visible|hidden,
                      text, has_class}; a list means all must hold)
  paused:            selector that is visible when the form is switched off
  success_response:  {status, json | body, content_type} for mock_success
  error_response:    same, for mock_error (default 500 {"ok": false})
  live_expect:       {status, json} the real backend must answer the honeypot
                     request with (default 200 {"ok": true})
  live_payload_overrides: {name: value} merged into the live request body;
                     values support {now_ms} placeholders (urlencoded bodies only)
  scenarios:         subset of SCENARIOS (default: all except live_honeypot)
  viewport:          viewport name (default: first profile viewport)

Check options:
  forms:             list of forms
  timeout_ms:        wait for the UI after submitting (default 8000)
  validation_wait_ms: how long to wait for an unwanted request (default 1500)
  screenshots:       on_failure | always | never (default on_failure)
"""

from __future__ import annotations

import json
from email.parser import BytesParser
from email.policy import default as email_policy
from typing import Any
from urllib.parse import parse_qs, urlencode

from monitor.checks.form_endpoint import expand
from monitor.models import CheckResult, Status

SCENARIOS = ["structure", "validation", "mock_success", "mock_error", "mock_network_error", "live_honeypot"]
DEFAULT_SCENARIOS = [s for s in SCENARIOS if s != "live_honeypot"]

HONEYPOT_VALUE = "website-monitor-honeypot"

FIELD_FACTS_JS = """(el) => {
  const style = getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  const label = (el.labels && el.labels.length ? [...el.labels].map(l => l.innerText).join(' ') : '')
    || el.getAttribute('aria-label') || '';
  const hiddenByAncestor = !!el.closest('[aria-hidden="true"], [hidden]');
  return {
    tag: el.tagName.toLowerCase(),
    name: el.getAttribute('name') || '',
    type: el.getAttribute('type') || '',
    required: el.required || el.getAttribute('aria-required') === 'true',
    label: label.trim(),
    disabled: el.disabled,
    tabindex: el.getAttribute('tabindex'),
    autocomplete: el.getAttribute('autocomplete'),
    perceivable: style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0'
                 && rect.width > 1 && rect.height > 1
                 && rect.right > 0 && rect.bottom > 0
                 && rect.left < window.innerWidth + 1 && !hiddenByAncestor,
  };
}"""


# ── helpers ──────────────────────────────────────────────────────────────────

def parse_payload(content_type: str, body: bytes | None) -> dict[str, Any]:
    """Decode a urlencoded, multipart or JSON request body into {name: value}."""
    if not body:
        return {}
    content_type = content_type or ""
    kind = content_type.lower()  # the multipart boundary itself is case-sensitive
    if "json" in kind:
        try:
            data = json.loads(body)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {"_": data}
    if "multipart/form-data" in kind:
        message = BytesParser(policy=email_policy).parsebytes(
            b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
        out: dict[str, Any] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if name:
                out[name] = (part.get_content().removesuffix("\r\n").removesuffix("\n")
                             if part.get_content_maintype() == "text" else "<file>")
        return out
    return {k: v[0] if len(v) == 1 else v
            for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()}


def _as_list(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    return list(value) if isinstance(value, list) else [value]


def _fulfil(route, spec: dict[str, Any]) -> None:
    body = spec.get("body")
    content_type = spec.get("content_type")
    if "json" in spec:
        body = json.dumps(spec["json"])
        content_type = content_type or "application/json"
    route.fulfill(status=int(spec.get("status", 200)), body=body or "",
                  content_type=content_type or "text/plain",
                  headers={"x-website-monitor": "mocked"})


class _Recorder:
    """Route handler that records every request to the endpoint."""

    def __init__(self, mode: str, response: dict[str, Any] | None = None, honeypot_name: str | None = None):
        self.mode = mode
        self.response = response or {}
        self.honeypot_name = honeypot_name
        self.overrides: dict[str, str] = {}
        self.sent_payload: dict[str, Any] | None = None
        self.requests: list[dict[str, Any]] = []
        self.refused: list[str] = []

    def __call__(self, route) -> None:
        request = route.request
        payload = parse_payload(request.headers.get("content-type", ""), request.post_data_buffer)
        self.requests.append({"method": request.method, "url": request.url, "payload": payload,
                              "content_type": request.headers.get("content-type", "")})
        if self.mode == "fulfil":
            _fulfil(route, self.response)
        elif self.mode == "network_error":
            route.abort("failed")
        elif self.mode == "live_if_honeypot":
            value = payload.get(self.honeypot_name or "", "")
            if request.method == "POST" and isinstance(value, str) and value.strip():
                if self.overrides:
                    if "x-www-form-urlencoded" not in request.headers.get("content-type", ""):
                        self.refused.append("live_payload_overrides need a urlencoded body — aborted")
                        route.abort("blockedbyclient")
                        return
                    body = {**payload, **self.overrides}
                    self.sent_payload = body
                    route.continue_(post_data=urlencode(body, doseq=True))
                else:
                    self.sent_payload = payload
                    route.continue_()
            else:
                self.refused.append(f"{request.method} {request.url} without honeypot value — aborted")
                route.abort("blockedbyclient")
        else:  # "block": nothing may reach the backend
            route.abort("blockedbyclient")


def _fill(page, fields: dict[str, Any]) -> None:
    for selector, value in (fields or {}).items():
        loc = page.locator(selector).first
        if isinstance(value, dict):
            if "select_index" in value:
                index = int(value["select_index"])
                page.wait_for_function("([s, i]) => { const el = document.querySelector(s);"
                                       " return el && el.options && el.options.length > i; }",
                                       arg=[selector, index], timeout=10000)
                loc.select_option(index=index)
            elif "select_value" in value:
                loc.select_option(value=str(value["select_value"]))
            elif "select_label" in value:
                loc.select_option(label=str(value["select_label"]))
            elif "check" in value:
                loc.set_checked(bool(value["check"]))
            else:
                raise ValueError(f"unsupported field spec for {selector}: {value}")
        else:
            loc.fill(str(value))


def _expectation_problems(page, expectations: list[dict[str, Any]], timeout_ms: int) -> list[str]:
    problems = []
    for exp in expectations:
        selector = exp.get("selector", "body")
        state = exp.get("state", "visible")
        loc = page.locator(selector).first
        try:
            loc.wait_for(state=state, timeout=timeout_ms)
        except Exception:
            problems.append(f"{selector} did not become {state}")
            continue
        if state == "hidden":
            continue
        if exp.get("text"):
            try:
                page.wait_for_function("([s, t]) => { const el = document.querySelector(s);"
                                       " return el && el.innerText.toLowerCase().includes(t.toLowerCase()); }",
                                       arg=[selector, exp["text"]], timeout=timeout_ms)
            except Exception:
                actual = (loc.inner_text() or "").strip().replace("\n", " ")[:120]
                problems.append(f"{selector} text {actual!r} lacks {exp['text']!r}")
        if exp.get("has_class"):
            try:
                page.wait_for_function("([s, c]) => { const el = document.querySelector(s);"
                                       " return el && el.classList.contains(c); }",
                                       arg=[selector, exp["has_class"]], timeout=timeout_ms)
            except Exception:
                problems.append(f"{selector} lacks class {exp['has_class']!r} "
                                f"(has {loc.get_attribute('class')!r})")
    return problems


# ── scenarios ────────────────────────────────────────────────────────────────

class _Run:
    """One scenario: fresh browser context, endpoint route, collected problems."""

    def __init__(self, ctx, options, form: dict[str, Any], scenario: str):
        self.ctx, self.options, self.form, self.scenario = ctx, options, form, scenario
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.details: list[str] = []
        self.timeout = int(options.get("timeout_ms", 8000))
        self.context, self.page = ctx.browser.new_page(form.get("viewport"))
        self.page.on("pageerror", lambda err: self.failures.append(f"uncaught JS error: {str(err)[:300]}"))

    @property
    def submit_selector(self) -> str:
        return self.form.get("submit") or f"{self.form['form']} [type=submit]"

    def open(self, recorder: _Recorder) -> bool:
        self.page.route(self.form["endpoint"], recorder)
        response = self.page.goto(self.ctx.url(self.form["path"]), wait_until="load")
        if response is None or response.status >= 400:
            self.failures.append(f"page returned HTTP {response.status if response else 'none'}")
            return False
        try:
            self.page.locator(self.form["form"]).first.wait_for(state="attached", timeout=self.timeout)
        except Exception:
            self.failures.append(f"form {self.form['form']} not found on {self.form['path']}")
            return False
        return True

    def paused(self) -> bool:
        selector = self.form.get("paused")
        return bool(selector) and self.page.locator(selector).count() > 0

    def submit(self) -> None:
        self.page.locator(self.submit_selector).first.click()

    def check_request(self, recorder: _Recorder) -> dict[str, Any] | None:
        if not recorder.requests:
            self.failures.append("submitting sent no request to the endpoint")
            return None
        if len(recorder.requests) > 1:
            self.failures.append(f"one submit sent {len(recorder.requests)} requests (double submit?)")
        req = recorder.requests[0]
        payload = req["payload"]
        self.details.append(f"request: {req['method']} {req['url']} ({req['content_type'] or 'no body type'})")
        self.details.append("payload keys: " + ", ".join(sorted(payload)) if payload else "payload: empty")
        expected_method = str(self.form.get("expect_method", "POST")).upper()
        if req["method"] != expected_method:
            self.failures.append(f"request method {req['method']}, expected {expected_method}")
        honeypot_name = self.honeypot_name()
        for key, value in (self.form.get("expect_payload") or {}).items():
            if self.scenario == "live_honeypot" and key == honeypot_name:
                continue  # filled on purpose
            if key not in payload:
                self.failures.append(f"payload lacks {key!r}")
            elif str(payload[key]) != str(value):
                self.failures.append(f"payload {key}={payload[key]!r}, expected {value!r}")
        for key in self.form.get("expect_payload_keys") or []:
            if not str(payload.get(key, "")).strip():
                self.failures.append(f"payload field {key!r} missing or empty")
        if honeypot_name and self.scenario != "live_honeypot" and str(payload.get(honeypot_name, "")).strip():
            self.failures.append(f"honeypot {honeypot_name!r} is filled for a human submit "
                                 "(autofill? the server would silently drop real messages)")
        return payload

    def honeypot_name(self) -> str | None:
        # Cached: some forms replace their markup after a successful submit.
        if getattr(self, "_honeypot_name", None) is None:
            selector = self.form.get("honeypot")
            if not selector or self.page.locator(selector).count() == 0:
                return None
            self._honeypot_name = self.page.locator(selector).first.get_attribute("name")
        return self._honeypot_name

    def finish(self) -> CheckResult:
        status = Status.FAIL if self.failures else Status.WARNING if self.warnings else Status.PASS
        artifacts = []
        shots = self.options.get("screenshots", "on_failure")
        if shots == "always" or (shots == "on_failure" and status != Status.PASS):
            try:
                shot = self.ctx.artifact_path("screenshots", f"forms_{self.form['name']}_{self.scenario}.png")
                self.page.screenshot(path=str(shot), full_page=True)
                artifacts.append(self.ctx.relative_artifact(shot))
            except Exception:
                pass
        self.context.close()
        issues = self.failures + self.warnings
        return CheckResult("forms", f"{self.form['name']} [{self.scenario}]", status,
                           "; ".join(issues[:3]) if issues else self.ok_message,
                           details=issues + self.details, artifacts=artifacts)

    ok_message = "OK"


def _structure(run: _Run) -> None:
    recorder = _Recorder("block")
    if not run.open(recorder):
        return
    page, form = run.page, run.form
    if run.paused():
        run.warnings.append(f"form is paused ({form['paused']} present) — visitors cannot submit it")
        return
    submit = page.locator(run.submit_selector).first
    if submit.count() == 0:
        run.failures.append(f"submit button {run.submit_selector} missing")
    elif not submit.is_visible() or not submit.is_enabled():
        run.failures.append("submit button is hidden or disabled")
    for selector in form.get("fields", {}):
        loc = page.locator(selector).first
        if loc.count() == 0:
            run.failures.append(f"field {selector} missing")
            continue
        facts = loc.evaluate(FIELD_FACTS_JS)
        if not facts["perceivable"]:
            run.failures.append(f"field {selector} is not visible")
        if facts["disabled"]:
            run.failures.append(f"field {selector} is disabled")
        if not facts["label"]:
            run.warnings.append(f"field {selector} has no label")
        if not facts["name"]:
            run.failures.append(f"field {selector} has no name attribute (its value is never sent)")
    for selector in form.get("required_fields", []):
        loc = page.locator(selector).first
        if loc.count() and not loc.evaluate(FIELD_FACTS_JS)["required"]:
            run.warnings.append(f"required field {selector} lacks the required attribute")
    if form.get("honeypot"):
        hp = page.locator(form["honeypot"]).first
        if hp.count() == 0:
            run.failures.append(f"honeypot {form['honeypot']} missing — spam protection gone")
        else:
            facts = hp.evaluate(FIELD_FACTS_JS)
            if facts["perceivable"]:
                run.failures.append("honeypot field is visible to humans")
            if facts["tabindex"] != "-1":
                run.warnings.append("honeypot is reachable by keyboard (set tabindex=-1)")
            if facts["autocomplete"] not in ("off", "new-password"):
                run.warnings.append("honeypot lacks autocomplete=off (password managers may fill it)")
            run.details.append(f"honeypot field name: {facts['name']!r}")
    if recorder.requests:
        run.failures.append("page sent a request to the form endpoint without being submitted")
    run.ok_message = f"{len(form.get('fields', {}))} field(s), submit button and honeypot in place"


def _validation(run: _Run) -> None:
    recorder = _Recorder("block")
    if not run.open(recorder) or run.paused():
        return
    form = run.form
    wait = int(run.options.get("validation_wait_ms", 1500))
    cases: list[tuple[str, dict[str, Any]]] = [("empty form", {})]
    fields = form.get("fields", {})
    for selector in form.get("required_fields", []):
        cases.append((f"without {selector}", {k: v for k, v in fields.items() if k != selector}))
    for invalid in form.get("invalid_values", []):
        cases.append((f"{invalid['field']}={invalid['value']!r}", {**fields, invalid["field"]: invalid["value"]}))

    for label, values in cases:
        run.page.reload(wait_until="load")
        run.page.locator(form["form"]).first.wait_for(state="attached", timeout=run.timeout)
        before = len(recorder.requests)
        try:
            _fill(run.page, values)
            run.submit()
        except Exception as exc:
            run.failures.append(f"{label}: could not fill/submit ({str(exc).splitlines()[0][:150]})")
            continue
        run.page.wait_for_timeout(wait)
        invalid_count = run.page.locator(f"{form['form']} :invalid").count()
        if len(recorder.requests) > before:
            run.failures.append(f"{label}: request was sent although the form is incomplete/invalid")
        else:
            run.details.append(f"{label}: blocked ({invalid_count} field(s) flagged :invalid)")
    run.ok_message = f"{len(cases)} incomplete/invalid submission(s) blocked in the browser"


def _mock_success(run: _Run) -> None:
    recorder = _Recorder("fulfil", run.form.get("success_response") or {"status": 200, "json": {"ok": True}})
    if not run.open(recorder) or run.paused():
        return
    run.honeypot_name()
    _fill(run.page, run.form.get("fields", {}))
    run.submit()
    run.failures += _expectation_problems(run.page, _as_list(run.form.get("success")), run.timeout)
    run.check_request(recorder)
    run.ok_message = "payload correct and success UI shown (backend mocked, nothing sent)"


def _mock_error(run: _Run) -> None:
    recorder = _Recorder("fulfil", run.form.get("error_response")
                         or {"status": 500, "json": {"ok": False, "message": "monitor: simulated failure"}})
    if not run.open(recorder) or run.paused():
        return
    _fill(run.page, run.form.get("fields", {}))
    run.submit()
    _after_failure(run, recorder, "server error")


def _mock_network_error(run: _Run) -> None:
    recorder = _Recorder("network_error")
    if not run.open(recorder) or run.paused():
        return
    _fill(run.page, run.form.get("fields", {}))
    run.submit()
    _after_failure(run, recorder, "network failure")


def _after_failure(run: _Run, recorder: _Recorder, what: str) -> None:
    run.failures += _expectation_problems(run.page, _as_list(run.form.get("error")), run.timeout)
    for exp in _as_list(run.form.get("success")):
        selector = exp.get("selector")
        if selector and exp.get("state", "visible") == "visible" and run.page.locator(selector).first.is_visible():
            if not exp.get("text") or exp["text"].lower() in run.page.locator(selector).first.inner_text().lower():
                run.failures.append(f"success UI ({selector}) shown although the {what} happened")
    if not recorder.requests:
        run.failures.append("submitting sent no request to the endpoint")
    if not _as_list(run.form.get("error")):
        run.warnings.append("no 'error' expectation configured; only checked that no success is shown")
    run.ok_message = f"{what} is shown to the user (backend mocked, nothing sent)"


def _live_honeypot(run: _Run) -> None:
    form = run.form
    recorder = _Recorder("live_if_honeypot")
    if not run.open(recorder) or run.paused():
        return
    honeypot_name = run.honeypot_name()
    if not honeypot_name:
        run.failures.append(f"honeypot {form.get('honeypot')} missing or unnamed — live test refused")
        return
    recorder.honeypot_name = honeypot_name
    recorder.overrides = {k: str(v) for k, v in
                          expand(form.get("live_payload_overrides") or {}, run.ctx.base_url).items()}
    _fill(run.page, form.get("fields", {}))
    run.page.locator(form["honeypot"]).first.evaluate(
        "(el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles: true})); }", HONEYPOT_VALUE)

    expect = form.get("live_expect") or {"status": 200, "json": {"ok": True}}
    try:
        with run.page.expect_response(form["endpoint"], timeout=run.timeout + 7000) as info:
            run.submit()
        response = info.value
    except Exception:
        response = None
    run.failures += [f"SAFETY: {r}" for r in recorder.refused]
    if response is None:
        if not recorder.refused:
            run.failures.append("no response from the live endpoint")
        return
    run.details.append(f"live endpoint answered HTTP {response.status}; payload had {honeypot_name}={HONEYPOT_VALUE!r}")
    if recorder.overrides:
        run.details.append("payload rewritten before sending (defence in depth): "
                           + ", ".join(f"{k}={v}" for k, v in recorder.overrides.items()))
    if response.status != int(expect.get("status", 200)):
        run.failures.append(f"live endpoint answered HTTP {response.status}, expected {expect.get('status', 200)}")
    if "json" in expect:
        try:
            body = response.json()
        except Exception:
            body = None
            run.failures.append(f"live endpoint did not answer JSON: {response.text()[:200]!r}")
        if isinstance(body, dict):
            for key, value in expect["json"].items():
                if body.get(key) != value:
                    run.failures.append(f"live response {key}={body.get(key)!r}, expected {value!r}")
    run.failures += _expectation_problems(run.page, _as_list(form.get("success")), run.timeout)
    run.check_request(recorder)
    run.ok_message = (f"real backend reached and accepted the bot submission "
                      f"(HTTP {response.status}, dropped by honeypot)")


SCENARIO_FUNCS = {
    "structure": _structure,
    "validation": _validation,
    "mock_success": _mock_success,
    "mock_error": _mock_error,
    "mock_network_error": _mock_network_error,
    "live_honeypot": _live_honeypot,
}


def run(ctx, options):
    forms = options.get("forms") or []
    if not forms:
        return [CheckResult("forms", ctx.base_url, Status.SKIP, "No forms configured")]
    results = []
    for i, form in enumerate(forms):
        form = {"name": form.get("name", f"form{i + 1}"), **form}
        paused = False
        for scenario in form.get("scenarios", DEFAULT_SCENARIOS):
            if paused and scenario != "structure":
                results.append(CheckResult("forms", f"{form['name']} [{scenario}]", Status.SKIP,
                                           "form is paused; submit scenarios skipped"))
                continue
            run_ = _Run(ctx, options, form, scenario)
            try:
                SCENARIO_FUNCS[scenario](run_)
                if form.get("paused") and scenario == "structure" and run_.paused():
                    paused = True
            except Exception as exc:
                run_.failures.append(f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}")
            results.append(run_.finish())
    return results
