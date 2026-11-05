import socket
import ssl
from datetime import datetime, timezone
from urllib.parse import urlparse

def run(site, options):
    parsed = urlparse(site["base_url"])
    host = parsed.hostname
    port = parsed.port or 443
    warn_days = int(options.get("warn_days_before_expiry", 21))
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=site.get("timeout_seconds", 20)) as sock:
            with context.wrap_socket(sock, server_hostname=host) as secure:
                cert = secure.getpeercert()
        expiry = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days = (expiry - datetime.now(timezone.utc)).days
        status = "fail" if days < 0 else ("warning" if days <= warn_days else "pass")
        return [{"check": "ssl", "target": host, "status": status,
                 "message": f"Certificate expires in {days} day(s), at {expiry.isoformat()}"}]
    except Exception as exc:
        return [{"check": "ssl", "target": host or site["base_url"], "status": "fail", "message": str(exc)}]
