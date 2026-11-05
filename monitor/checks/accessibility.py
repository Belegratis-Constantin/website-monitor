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
                html = soup.find("html")
                if not html or not html.get("lang"):
                    issues.append("document language is missing")
                for img in soup.find_all("img"):
                    if not img.has_attr("alt"):
                        issues.append("image missing alt attribute")
                for control in soup.find_all(["input", "select", "textarea"]):
                    if control.get("type") == "hidden":
                        continue
                    control_id = control.get("id")
                    has_label = bool(control_id and soup.find("label", attrs={"for": control_id}))
                    has_aria = bool(control.get("aria-label") or control.get("aria-labelledby"))
                    if not has_label and not has_aria:
                        issues.append(f"form control without detectable label: {control.name}#{control_id or 'no-id'}")
                results.append({"check": "accessibility", "target": url,
                                "status": "warning" if issues else "pass",
                                "message": "; ".join(issues[:12]) if issues else "Basic HTML accessibility checks passed"})
            except Exception as exc:
                results.append({"check": "accessibility", "target": url, "status": "fail", "message": str(exc)})
    return results
