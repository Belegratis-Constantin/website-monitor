from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from monitor.checks.common import session, join_url

def run(site, options):
    base_host = urlparse(site["base_url"]).netloc
    limit = int(options.get("max_assets", 150))
    found, failures, checked = set(), [], 0
    with session(site) as s:
        for path in site.get("paths", ["/"]):
            try:
                page_url = join_url(site["base_url"], path)
                response = s.get(page_url, timeout=site.get("timeout_seconds", 20))
                soup = BeautifulSoup(response.text, "html.parser")
                for tag, attr in (("script", "src"), ("img", "src"), ("link", "href")):
                    for el in soup.find_all(tag):
                        raw = el.get(attr)
                        if not raw or raw.startswith(("data:", "mailto:", "#")):
                            continue
                        url = urljoin(page_url, raw)
                        if urlparse(url).netloc == base_host:
                            found.add(url)
            except Exception as exc:
                failures.append(f"{path}: {exc}")
        for url in list(found)[:limit]:
            checked += 1
            try:
                r = s.get(url, timeout=site.get("timeout_seconds", 20), stream=True)
                code = r.status_code
                r.close()
                if code >= 400:
                    failures.append(f"{url} returned HTTP {code}")
            except Exception as exc:
                failures.append(f"{url}: {exc}")
    msg = f"Checked {checked} same-origin assets; {len(failures)} issue(s). "
    msg += " | ".join(failures[:12]) if failures else "All checked assets loaded"
    return [{"check": "assets", "target": site["base_url"], "status": "warning" if failures else "pass", "message": msg}]
