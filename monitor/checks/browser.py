from playwright.sync_api import sync_playwright
from monitor.checks.common import join_url

def run(site, options):
    results = []
    paths = options.get("paths", site.get("paths", ["/"]))
    viewports = options.get("viewports", [{"name": "desktop", "width": 1365, "height": 900}])
    forms = {item["path"]: item for item in options.get("forms", [])}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=bool(options.get("headless", True)))
        try:
            for path in paths:
                url = join_url(site["base_url"], path)
                for viewport in viewports:
                    errors = []
                    page = browser.new_page(viewport={"width": int(viewport["width"]), "height": int(viewport["height"])})
                    page.on("pageerror", lambda error: errors.append(f"uncaught JS error: {error}"))
                    page.on("requestfailed", lambda req: errors.append(f"failed same-origin request: {req.url}") if req.url.startswith(site["base_url"]) else None)
                    try:
                        response = page.goto(url, wait_until="domcontentloaded", timeout=int(options.get("navigation_timeout_ms", 30000)))
                        code = response.status if response else None
                        if code is None or code >= 400:
                            errors.append(f"navigation returned HTTP {code}")
                        page.locator("body").wait_for(state="visible", timeout=10000)
                        if not page.title().strip():
                            errors.append("document title is empty")
                        if not page.locator("body").inner_text().strip():
                            errors.append("page body is empty")
                        overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
                        if overflow:
                            errors.append("horizontal page overflow at this viewport")
                        if path in forms:
                            cfg = forms[path]
                            if page.locator("form").count() == 0:
                                errors.append("expected form not found")
                            if cfg.get("require_email") and page.locator('input[type="email"]').count() == 0:
                                errors.append("expected email input not found")
                            if cfg.get("require_select") and page.locator("select").count() == 0:
                                errors.append("expected select field not found")
                        results.append({"check": "browser", "target": f"{url} [{viewport['name']}]",
                                        "status": "fail" if errors else "pass",
                                        "message": "; ".join(errors) if errors else f"Loaded in Chromium; title: {page.title()}"})
                    except Exception as exc:
                        results.append({"check": "browser", "target": f"{url} [{viewport['name']}]", "status": "fail", "message": str(exc)})
                    finally:
                        page.close()
            if options.get("check_language_switch", False):
                page = browser.new_page()
                try:
                    page.goto(site["base_url"], wait_until="domcontentloaded", timeout=int(options.get("navigation_timeout_ms", 30000)))
                    button_text = options.get("language_button_text", "EN")
                    toggle_text = options.get("language_toggle_text", "DE")
                    button = page.get_by_role("button", name=button_text, exact=True).first
                    if button.count() == 0:
                        results.append({"check": "browser-language", "target": site["base_url"], "status": "warning", "message": f"Language switch '{button_text}' not detected; verify selector"})
                    else:
                        before = page.locator("body").inner_text()
                        button.click(timeout=5000)
                        page.wait_for_timeout(1000)
                        after = page.locator("body").inner_text()
                        switched = toggle_text.casefold() in after.casefold() or before != after
                        results.append({"check": "browser-language", "target": site["base_url"],
                                        "status": "pass" if switched else "warning",
                                        "message": "Language switch changed page state" if switched else "No clear language change detected after click"})
                except Exception as exc:
                    results.append({"check": "browser-language", "target": site["base_url"], "status": "warning", "message": str(exc)})
                finally:
                    page.close()
        finally:
            browser.close()
    return results
