import time
from monitor.checks.common import session, join_url

def run(site, options):
    results = []
    expected = int(options.get("expected_status", 200))
    threshold = int(options.get("max_response_time_ms", 3000))
    with session(site) as s:
        for path in site.get("paths", ["/"]):
            url = join_url(site["base_url"], path)
            started = time.perf_counter()
            try:
                response = s.get(url, timeout=site.get("timeout_seconds", 20), allow_redirects=True)
                elapsed = round((time.perf_counter() - started) * 1000)
                ok = response.status_code == expected
                status = "pass" if ok and elapsed <= threshold else ("warning" if ok else "fail")
                message = f"HTTP {response.status_code}; {elapsed} ms; final URL {response.url}"
                if elapsed > threshold:
                    message += f"; slower than {threshold} ms threshold"
                results.append({"check": "uptime", "target": url, "status": status, "message": message,
                                "status_code": response.status_code, "response_time_ms": elapsed})
            except requests.RequestException as exc:
                results.append({"check": "uptime", "target": url, "status": "fail", "message": str(exc)})
    return results
