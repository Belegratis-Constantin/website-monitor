"""Scripted browser journeys: click, type and assert, step by step.

Each interaction is a named list of steps run on a fresh page:

  - click: "#menu-button"
  - fill: {selector: "#q", value: "hello"}
  - select: {selector: "#lang", value: "en"}       # or index / label
  - press: {selector: "body", key: "Escape"}       # selector optional
  - hover: ".dropdown"
  - wait: 300                                      # milliseconds
  - wait_for: ".panel"                             # visible
  - expect_visible: "#topbar-nav"
  - expect_hidden: "#topbar-nav"
  - expect_text: {selector: "h1", text: "Hello"}   # selector optional (body)
  - expect_attribute: {selector: "html", name: "data-theme", value: "dark"}
                                                   # or `changes: true` vs. the value
                                                   # read by a previous `remember`
  - remember: {selector: "html", name: "data-theme"}
  - expect_url: "/events"                          # substring or regex
  - expect_count: {selector: ".card", min: 1, max: 50}
  - expect_focus: "#dialog-close"

Per interaction:
  name, path, steps, viewport (default first profile viewport),
  skip_unless_visible: selector — SKIP the interaction when it is not
  visible (e.g. a feature the site switched off), instead of failing.

Options:
  interactions: list as above
  step_timeout_ms: default 5000
  screenshots: on_failure | always | never (default on_failure)
"""

from __future__ import annotations

import re
from typing import Any

from monitor.models import CheckResult, Status


def _spec(step_value: Any, key: str = "selector") -> dict[str, Any]:
    return step_value if isinstance(step_value, dict) else {key: step_value}


def _attr(page, selector: str, name: str) -> str | None:
    return page.locator(selector).first.get_attribute(name)


def _run_step(page, action: str, value: Any, memory: dict[str, Any], timeout: int) -> None:
    s = _spec(value)
    loc = page.locator(s["selector"]).first if "selector" in s else None
    if action == "click":
        loc.click(timeout=timeout)
    elif action == "fill":
        loc.fill(str(s["value"]), timeout=timeout)
    elif action == "select":
        kwargs = {k: s[k] for k in ("value", "index", "label") if k in s}
        loc.select_option(**kwargs, timeout=timeout)
    elif action == "press":
        (loc or page.locator("body")).press(s["key"], timeout=timeout)
    elif action == "hover":
        loc.hover(timeout=timeout)
    elif action == "wait":
        page.wait_for_timeout(int(value))
    elif action == "wait_for":
        loc.wait_for(state=s.get("state", "visible"), timeout=timeout)
    elif action == "expect_visible":
        try:
            loc.wait_for(state="visible", timeout=timeout)
        except Exception:
            raise AssertionError(f"{s['selector']} is not visible") from None
    elif action == "expect_hidden":
        try:
            loc.wait_for(state="hidden", timeout=timeout)
        except Exception:
            raise AssertionError(f"{s['selector']} is still visible") from None
    elif action == "expect_text":
        selector = s.get("selector", "body")
        try:
            page.wait_for_function("([sel, t]) => { const el = document.querySelector(sel);"
                                   " return el && el.innerText.toLowerCase().includes(t.toLowerCase()); }",
                                   arg=[selector, s["text"]], timeout=timeout)
        except Exception:
            raise AssertionError(f"{selector} does not contain {s['text']!r}") from None
    elif action == "remember":
        memory[(s["selector"], s["name"])] = _attr(page, s["selector"], s["name"])
    elif action == "expect_attribute":
        key = (s["selector"], s["name"])
        deadline_ms, actual = timeout, None
        for _ in range(max(1, deadline_ms // 100)):
            actual = _attr(page, s["selector"], s["name"])
            if "value" in s and actual == str(s["value"]):
                return
            if s.get("changes") and key in memory and actual != memory[key]:
                return
            if "value" not in s and not s.get("changes") and actual is not None:
                return
            page.wait_for_timeout(100)
        if s.get("changes"):
            raise AssertionError(f"{s['selector']}[{s['name']}] stayed {actual!r}")
        raise AssertionError(f"{s['selector']}[{s['name']}] is {actual!r}, expected {s.get('value')!r}")
    elif action == "expect_url":
        pattern = str(value)
        for _ in range(max(1, timeout // 100)):
            if pattern in page.url or re.search(pattern, page.url):
                return
            page.wait_for_timeout(100)
        raise AssertionError(f"URL {page.url} does not match {pattern!r}")
    elif action == "expect_count":
        count = page.locator(s["selector"]).count()
        if count < int(s.get("min", 1)) or ("max" in s and count > int(s["max"])):
            raise AssertionError(f"{s['selector']} matched {count} element(s), expected "
                                 f"{s.get('min', 1)}–{s.get('max', '∞')}")
    elif action == "expect_focus":
        if not loc.evaluate("el => el === document.activeElement"):
            raise AssertionError(f"{s['selector']} does not have focus")
    else:
        raise ValueError(f"unknown step '{action}'")


def _interaction(ctx, options, spec: dict[str, Any]) -> CheckResult:
    name = spec.get("name", spec.get("path", "interaction"))
    timeout = int(options.get("step_timeout_ms", 5000))
    context, page = ctx.browser.new_page(spec.get("viewport"))
    target = f"{name} [{ctx.browser.viewport(spec.get('viewport'))[0]}]"
    errors: list[str] = []
    page.on("pageerror", lambda err: errors.append(f"uncaught JS error: {str(err)[:300]}"))
    status, message, details, done = Status.PASS, "", [], 0
    steps = spec.get("steps", [])
    try:
        page.goto(ctx.url(spec.get("path", "/")), wait_until="load")
        gate = spec.get("skip_unless_visible")
        if gate and not page.locator(gate).first.is_visible():
            context.close()
            return CheckResult("interactions", target, Status.SKIP, f"{gate} is not visible; interaction skipped")
        memory: dict[str, Any] = {}
        for i, step in enumerate(steps, 1):
            if not isinstance(step, dict) or len(step) != 1:
                raise ValueError(f"step {i} must be a single-key mapping, got {step!r}")
            action, value = next(iter(step.items()))
            try:
                _run_step(page, action, value, memory, timeout)
            except AssertionError as exc:
                status, message = Status.FAIL, f"step {i} ({action}): {exc}"
                break
            except Exception as exc:
                status, message = Status.FAIL, f"step {i} ({action}) failed: {str(exc).splitlines()[0][:200]}"
                break
            details.append(f"✓ {action}: {value}")
            done = i
    except Exception as exc:
        status, message = Status.FAIL, f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
    if errors:
        status = Status.FAIL
        message = "; ".join(filter(None, [message, *errors[:2]]))
    artifacts = []
    shots = options.get("screenshots", "on_failure")
    if shots == "always" or (shots == "on_failure" and status != Status.PASS):
        try:
            shot = ctx.artifact_path("screenshots", f"interaction_{name}.png")
            page.screenshot(path=str(shot), full_page=False)
            artifacts.append(ctx.relative_artifact(shot))
        except Exception:
            pass
    context.close()
    return CheckResult("interactions", target, status, message or f"all {done} step(s) passed",
                       details=details + errors, artifacts=artifacts)


def run(ctx, options):
    interactions = options.get("interactions") or []
    if not interactions:
        return [CheckResult("interactions", ctx.base_url, Status.SKIP, "No interactions configured")]
    return [_interaction(ctx, options, spec) for spec in interactions]
