from urllib.parse import urljoin, urlparse, urldefrag
from bs4 import BeautifulSoup
from monitor.checks.common import session, join_url

def run(site, options):
    base = site["base_url"]
    base_host = urlparse(base).netloc
    max_pages = int(options.get("max_pages", 10))
    max_links = int(options.get("max_links", 100))
    check_external = bool(options.get("check_external", False))
    check_fragments = bool(options.get("check_fragments", True))
    queue = [join_url(base, p) for p in site.get("paths", ["/"])]
    visited, checked, broken = set(), 0, []
    with session(site) as s:
        while queue and len(visited) < max_pages and checked < max_links:
            page = urldefrag(queue.pop(0))[0]
            if page in visited:
                continue
            visited.add(page)
            try:
                response = s.get(page, timeout=site.get("timeout_seconds", 20))
                if response.status_code >= 400:
                    broken.append(f"{page} returned HTTP {response.status_code}")
                    continue
                soup = BeautifulSoup(response.text, "html.parser")
                if check_fragments:
                    ids = {el.get("id") for el in soup.find_all(attrs={"id": True})}
                    for anchor in soup.find_all("a", href=True):
                        href = anchor["href"]
                        if href.startswith("#") and len(href) > 1 and href[1:] not in ids:
                            broken.append(f"{page} has missing anchor target {href}")
                for tag in soup.find_all(["a", "img", "script", "link"]):
                    attr = "href" if tag.name in ("a", "link") else "src"
                    raw = tag.get(attr)
                    if not raw or raw.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
                        continue
                    target = urljoin(page, raw)
                    internal = urlparse(target).netloc == base_host
                    clean_target = urldefrag(target)[0]
                    if internal and tag.name == "a" and clean_target.startswith(base) and clean_target not in visited:
                        queue.append(clean_target)
                    if not internal and not check_external:
                        continue
                    if checked >= max_links:
                        break
                    checked += 1
                    try:
                        r = s.get(target, allow_redirects=True, timeout=site.get("timeout_seconds", 20), stream=True)
                        code = r.status_code
                        r.close()
                        if code >= 400:
                            broken.append(f"{target} returned HTTP {code}")
                    except Exception as exc:
                        broken.append(f"{target}: {exc}")
            except Exception as exc:
                broken.append(f"{page}: {exc}")
    details = " | ".join(broken[:15]) if broken else "no broken internal links/resources found"
    return [{"check": "links", "target": base, "status": "warning" if broken else "pass",
             "message": f"Visited {len(visited)} page(s), checked {checked} links/resources; {len(broken)} issue(s). {details}"}]
