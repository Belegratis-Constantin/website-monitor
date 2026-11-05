from importlib import import_module

CHECKS = {
    "uptime": "monitor.checks.uptime",
    "links": "monitor.checks.links",
    "assets": "monitor.checks.assets",
    "headers": "monitor.checks.headers",
    "seo": "monitor.checks.seo",
    "content": "monitor.checks.content",
    "ssl": "monitor.checks.ssl_check",
    "accessibility": "monitor.checks.accessibility",
    "browser": "monitor.checks.browser",
}

def available_checks():
    return sorted(CHECKS)

def get_check(name):
    if name not in CHECKS:
        raise ValueError(f"Unknown check '{name}'. Available: {', '.join(available_checks())}")
    return import_module(CHECKS[name])
