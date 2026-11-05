from bs4 import BeautifulSoup
from monitor.checks.common import session, join_url

def run(site, options):
    results = []
    for path, phrases in options.get("required", {}).items():
        url = join_url(site["base_url"], path)
        try:
            with session(site) as s:
                response = s.get(url, timeout=site.get("timeout_seconds", 20))
            text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True).casefold()
            missing = [phrase for phrase in phrases if phrase.casefold() not in text]
            status = "fail" if response.status_code >= 400 else ("warning" if missing else "pass")
            message = f"HTTP {response.status_code}; missing expected text: {', '.join(missing)}" if missing else f"Expected text found; HTTP {response.status_code}"
            results.append({"check": "content", "target": url, "status": status, "message": message})
        except Exception as exc:
            results.append({"check": "content", "target": url, "status": "fail", "message": str(exc)})
    return results
