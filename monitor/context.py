"""Per-run state shared between checks: config, HTTP client, browser, output dir."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from monitor.http import HttpClient, join_url


class BrowserUnavailable(RuntimeError):
    pass


class BrowserSession:
    """Lazily started Chromium shared by all browser-based checks of a run.

    Every page it creates has the profile's ``safety.block_urls`` routed to an
    immediate abort, so no browser check can submit a real form by accident.
    The ``forms`` check installs page-level routes, which take precedence.
    """

    def __init__(self, site: dict[str, Any], headless: bool = True):
        self.site = site
        self.headless = headless
        self._playwright = None
        self._browser = None
        self.blocked_requests: list[str] = []

    def _start(self):
        if self._browser is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:  # pragma: no cover - depends on env
                raise BrowserUnavailable("playwright is not installed (pip install playwright)") from exc
            self._playwright = sync_playwright().start()
            try:
                self._browser = self._playwright.chromium.launch(headless=self.headless)
            except Exception as exc:
                self._playwright.stop()
                self._playwright = None
                raise BrowserUnavailable(
                    f"Chromium could not be launched ({exc.__class__.__name__}); run 'playwright install chromium'"
                ) from exc
        return self._browser

    def viewport(self, name_or_spec: str | dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        viewports = self.site.get("viewports", {})
        if name_or_spec is None:
            name_or_spec = next(iter(viewports), "desktop")
        if isinstance(name_or_spec, dict):
            spec = dict(name_or_spec)
            return str(spec.pop("name", f"{spec['width']}x{spec['height']}")), spec
        if name_or_spec not in viewports:
            raise ValueError(f"unknown viewport '{name_or_spec}' (defined: {', '.join(viewports)})")
        return name_or_spec, dict(viewports[name_or_spec])

    def new_page(self, viewport: str | dict[str, Any] | None = None, **context_options: Any):
        """Return ``(context, page)``; the caller must close the context."""
        browser = self._start()
        _, spec = self.viewport(viewport)
        options: dict[str, Any] = {
            "viewport": {"width": int(spec["width"]), "height": int(spec["height"])},
            "locale": self.site.get("browser_locale", "de-DE"),
            "ignore_https_errors": False,
        }
        for key in ("is_mobile", "has_touch", "device_scale_factor"):
            if key in spec:
                options[key] = spec[key]
        if self.site.get("browser_user_agent"):
            options["user_agent"] = self.site["browser_user_agent"]
        options.update(context_options)
        context = browser.new_context(**options)
        context.set_default_timeout(15000)
        for pattern in self.site.get("safety", {}).get("block_urls", []) or []:
            context.route(pattern, self._block)
        if self.site.get("browser_init_script"):
            context.add_init_script(self.site["browser_init_script"])
        return context, context.new_page()

    def _block(self, route) -> None:
        self.blocked_requests.append(f"{route.request.method} {route.request.url}")
        route.abort("blockedbyclient")

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None


class RunContext:
    def __init__(self, site: dict[str, Any], output_dir: Path, *, headless: bool = True):
        self.site = site
        self.base_url: str = site["base_url"]
        self.output_dir = output_dir
        self.http = HttpClient(site)
        self._browser: BrowserSession | None = None
        self.headless = headless

    def url(self, path: str) -> str:
        return join_url(self.base_url, path)

    @property
    def paths(self) -> list[str]:
        return list(self.site.get("paths", ["/"]))

    @property
    def browser(self) -> BrowserSession:
        if self._browser is None:
            self._browser = BrowserSession(self.site, headless=self.headless)
        return self._browser

    def artifact_path(self, *parts: str) -> Path:
        safe = [re.sub(r"[^A-Za-z0-9._-]+", "_", p).strip("_") or "_" for p in parts]
        path = self.output_dir.joinpath(*safe)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def relative_artifact(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.output_dir))
        except ValueError:
            return str(path)

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        self.http.close()
