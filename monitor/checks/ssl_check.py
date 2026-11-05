"""TLS certificate checks for the site host and any extra hosts.

Options:
  warn_days_before_expiry:  default 21
  fail_days_before_expiry:  default 7
  hosts:                    extra hostnames to check (e.g. www / alias domains)
  min_tls_version:          "TLSv1.2" (default) or "TLSv1.3"
"""

from __future__ import annotations

import socket
import ssl
from datetime import datetime, timezone
from urllib.parse import urlparse

from monitor.models import CheckResult, Status

TLS_ORDER = ["SSLv3", "TLSv1", "TLSv1.1", "TLSv1.2", "TLSv1.3"]


def _check_host(host: str, port: int, timeout: float, warn_days: int, fail_days: int, min_tls: str) -> CheckResult:
    target = f"{host}:{port}" if port != 443 else host
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as secure:
                cert = secure.getpeercert()
                version = secure.version() or "unknown"
    except ssl.SSLCertVerificationError as exc:
        return CheckResult("ssl", target, Status.FAIL, f"certificate not trusted: {exc.verify_message or exc}")
    except (OSError, ssl.SSLError) as exc:
        return CheckResult("ssl", target, Status.FAIL, f"TLS connection failed: {exc}")

    expiry = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    days = (expiry - datetime.now(timezone.utc)).days
    issuer = dict(x[0] for x in cert.get("issuer", ())).get("organizationName", "unknown issuer")
    sans = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]

    status, notes = Status.PASS, []
    if days < fail_days:
        status = Status.FAIL
        notes.append("expires within the failure window" if days >= 0 else "EXPIRED")
    elif days <= warn_days:
        status = Status.WARNING
        notes.append("renewal due soon — check that auto-renewal works")
    if version in TLS_ORDER and TLS_ORDER.index(version) < TLS_ORDER.index(min_tls):
        status = Status.FAIL
        notes.append(f"negotiated {version}, below {min_tls}")

    message = f"valid for {days} more day(s) (until {expiry:%Y-%m-%d}); {version}; issued by {issuer}"
    if notes:
        message += "; " + "; ".join(notes)
    return CheckResult("ssl", target, status, message, details=[f"SAN: {', '.join(sans)}"] if sans else [],
                       metrics={"days_remaining": days, "tls_version": version})


def run(ctx, options):
    parsed = urlparse(ctx.base_url)
    if parsed.scheme != "https":
        return [CheckResult("ssl", ctx.base_url, Status.SKIP, "Site is not served over HTTPS")]
    port = parsed.port or 443
    hosts = [parsed.hostname] + [h for h in options.get("hosts", []) if h != parsed.hostname]
    timeout = float(ctx.site.get("timeout_seconds", 20))
    return [
        _check_host(host, port, timeout,
                    int(options.get("warn_days_before_expiry", 21)),
                    int(options.get("fail_days_before_expiry", 7)),
                    str(options.get("min_tls_version", "TLSv1.2")))
        for host in hosts
    ]
