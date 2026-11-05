from bs4 import BeautifulSoup
from monitor.checks.common import session, join_url

def run(site, options):
    results = []
    with session(site) as s:
        for path in site.get("paths", ["/"]):
            url = join_url(site["base_url"], path)
            try:
                response = s.get(url, timeout=site.get("timeout_seconds", 20))
                soup = BeautifulSoup(response.text, "html.parser")
                issues = []
                if not soup.title or not soup.title.get_text(strip=True): issues.append("missing title")
                desc = soup.find("meta", attrs={"name": "description"})
                if not desc or not desc.get("content", "").strip(): issues.append("missing meta description")
                if not soup.find("link", rel="canonical"): issues.append("missing canonical URL")
                if not soup.find("h1"): issues.append("missing H1")
                html = soup.find("html")
                if not html or not html.get("lang"): issues.append("missing html lang attribute")
                if not soup.find("meta", property="og:title"): issues.append("missing Open Graph title")
                missing_alt = sum(1 for img in soup.find_all("img") if not img.has_attr("alt"))
                if missing_alt: issues.append(f"{missing_alt} image(s) without alt attribute")
                results.append({"check": "seo", "target": url, "status": "warning" if issues else "pass",
                                "message": "; ".join(issues) if issues else "Basic SEO metadata checks passed"})
            except Exception as exc:
                results.append({"check": "seo", "target": url, "status": "fail", "message": str(exc)})
    return results
