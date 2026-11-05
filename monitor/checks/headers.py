from monitor.checks.common import session, join_url

RECOMMENDED = {
    "strict-transport-security": "HSTS",
    "content-security-policy": "Content Security Policy",
    "x-content-type-options": "X-Content-Type-Options",
    "referrer-policy": "Referrer Policy",
    "permissions-policy": "Permissions Policy",
}

def run(site, options):
    results = []
    with session(site) as s:
        for path in site.get("paths", ["/"]):
            url = join_url(site["base_url"], path)
            try:
                response = s.get(url, timeout=site.get("timeout_seconds", 20))
                missing = [label for header, label in RECOMMENDED.items() if header not in response.headers]
                results.append({
                    "check": "headers", "target": url,
                    "status": "warning" if missing else "pass",
                    "message": "Missing recommended headers: " + ", ".join(missing) if missing else "All checked security headers are present",
                })
            except Exception as exc:
                results.append({"check": "headers", "target": url, "status": "fail", "message": str(exc)})
    return results
