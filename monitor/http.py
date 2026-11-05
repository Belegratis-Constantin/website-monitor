"""Shared, polite HTTP client used by every non-browser check.

* One ``requests.Session`` per run (connection reuse).
* A minimum delay between requests, so the monitor doesn't trip the host's
  flood protection and then report its own ban as an outage.
* Automatic retries for idempotent requests on connection errors / 502-504.
* A per-run cache: a page fetched by ``uptime`` is reused by ``seo``,
  ``content``, ``headers`` … instead of being downloaded five times.
* Soft-404 detection for hosts that answer unknown URLs with ``200`` + the
  homepage (SPA-style fallbacks), which would otherwise hide broken links.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass
from functools import cached_property
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

HOMEPAGE_PATHS = {"", "/", "/index.html", "/index.htm", "/index.php"}


def join_url(base: str, path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


def same_site(url: str, base_url: str) -> bool:
    return urlparse(url).netloc.lower() == urlparse(base_url).netloc.lower()


def body_fingerprint(content: bytes) -> str:
    return hashlib.sha256(content.strip()).hexdigest()


class HostUnreachable(requests.exceptions.ConnectionError):
    """Raised without network access once the circuit breaker for a host is open."""


@dataclass
class Page:
    url: str
    response: requests.Response

    @property
    def status(self) -> int:
        return self.response.status_code

    @property
    def final_url(self) -> str:
        return self.response.url

    @property
    def is_html(self) -> bool:
        return "html" in self.response.headers.get("content-type", "").lower()

    @cached_property
    def soup(self) -> BeautifulSoup:
        return BeautifulSoup(self.response.text, "html.parser")

    @cached_property
    def text(self) -> str:
        soup = BeautifulSoup(self.response.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "template"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)

    def absolute(self, href: str) -> str:
        return urljoin(self.final_url, href)


class HttpClient:
    def __init__(self, site: dict[str, Any]):
        self.site = site
        self.base_url: str = site["base_url"]
        # (connect, read): an unreachable/banning host should fail fast.
        self.timeout = (min(float(site.get("connect_timeout_seconds", 8)), float(site.get("timeout_seconds", 20))),
                        float(site.get("timeout_seconds", 20)))
        self.delay: float = float(site.get("request_delay_ms", 0)) / 1000
        self.requests_made = 0
        self._last_request = 0.0
        self._lock = threading.Lock()
        self._cache: dict[str, requests.Response] = {}
        self._soft404: str | None | bool = False  # False = not probed yet
        # Circuit breaker: after N consecutive connection failures to a host,
        # fail further requests to it immediately instead of waiting for
        # timeouts on every URL.
        self._connect_failures: dict[str, int] = {}
        self.circuit_threshold = int(site.get("circuit_breaker_failures", 3))

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": site.get("user_agent", "WebsiteMonitor/2.0"),
            "Accept-Language": site.get("accept_language", "de,en;q=0.8"),
        })
        retry = Retry(total=int(site.get("retries", 2)), connect=int(site.get("retries", 2)),
                      backoff_factor=1.0, status_forcelist=(502, 503, 504),
                      allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}), raise_on_status=False)
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    # ── low level ────────────────────────────────────────────────────────
    def _throttle(self) -> None:
        with self._lock:
            wait = self._last_request + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            self.requests_made += 1

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Uncached request. POSTs are never retried (see Retry config)."""
        url = join_url(self.base_url, url)
        host = urlparse(url).netloc.lower()
        if self._connect_failures.get(host, 0) >= self.circuit_threshold:
            raise HostUnreachable(f"{host} skipped: {self.circuit_threshold} consecutive connection failures this run")
        self._throttle()
        kwargs.setdefault("timeout", self.timeout)
        try:
            response = self.session.request(method, url, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            self._connect_failures[host] = self._connect_failures.get(host, 0) + 1
            raise
        self._connect_failures[host] = 0
        return response

    def circuit_open(self, url: str) -> bool:
        """True once this run has given up on the URL's host after repeated connection failures."""
        host = urlparse(join_url(self.base_url, url)).netloc.lower()
        return self._connect_failures.get(host, 0) >= self.circuit_threshold

    def get(self, url: str, *, cached: bool = True, **kwargs: Any) -> requests.Response:
        url = join_url(self.base_url, url)
        if cached and not kwargs and url in self._cache:
            return self._cache[url]
        response = self.request("GET", url, **kwargs)
        if cached and not kwargs:
            self._cache[url] = response
        return response

    def head_or_get(self, url: str) -> requests.Response:
        """Cheap existence check: reuse a cached GET, else stream a GET (no body download)."""
        url = join_url(self.base_url, url)
        if url in self._cache:
            return self._cache[url]
        response = self.request("GET", url, stream=True, allow_redirects=True)
        response.close()
        return response

    def page(self, path_or_url: str) -> Page:
        url = join_url(self.base_url, path_or_url)
        return Page(url, self.get(url))

    # ── soft 404 ─────────────────────────────────────────────────────────
    def soft404_fingerprint(self) -> str | None:
        """Body fingerprint the server returns (with HTTP 200) for unknown URLs, or None."""
        if self._soft404 is False:
            probe = join_url(self.base_url, f"/__website-monitor-404-probe-{secrets.token_hex(6)}")
            try:
                response = self.request("GET", probe)
                self._soft404 = body_fingerprint(response.content) if response.status_code == 200 else None
            except requests.RequestException:
                self._soft404 = None
        return self._soft404 or None

    def is_soft404(self, url: str, response: requests.Response) -> bool:
        if response.status_code != 200 or urlparse(url).path in HOMEPAGE_PATHS:
            return False
        if not same_site(url, self.base_url):
            return False
        fingerprint = self.soft404_fingerprint()
        return fingerprint is not None and body_fingerprint(response.content) == fingerprint

    def close(self) -> None:
        self.session.close()


def describe_error(exc: BaseException) -> str:
    """Short, human readable description of a requests exception."""
    text = str(exc)
    if isinstance(exc, HostUnreachable):
        return text
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "connection timed out (host down, firewalled, or the monitor's IP is rate-limited/banned)"
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "server accepted the connection but did not answer in time"
    if isinstance(exc, requests.exceptions.SSLError):
        m = re.search(r"certificate verify failed: (.+?)(?:\s*\(_ssl|\"\)|$)", text)
        return f"TLS error: {m.group(1).strip()}" if m else f"TLS error: {text[-200:]}"
    if isinstance(exc, requests.exceptions.ConnectionError):
        if "Name or service not known" in text or "nodename nor servname" in text or "getaddrinfo" in text:
            return "DNS lookup failed"
        if "Connection refused" in text:
            return "connection refused"
        return f"connection error: {text[:200]}"
    return f"{type(exc).__name__}: {text[:200]}"
