from monitor.checks import get_check

def run_checks(site: dict, selected=None) -> list[dict]:
    configured = site.get("checks", {})
    names = selected or [
        name for name, options in configured.items()
        if options.get("enabled", False)
    ]
    results = []
    for name in names:
        if name not in configured and selected is None:
            continue
        options = configured.get(name, {})
        if selected is None and not options.get("enabled", False):
            continue
        try:
            check = get_check(name)
            results.extend(check.run(site, options))
        except Exception as exc:
            results.append({
                "check": name,
                "target": site.get("base_url", ""),
                "status": "fail",
                "message": f"Check crashed: {type(exc).__name__}: {exc}",
            })
    return results
