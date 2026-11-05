from monitor.checks import CHECKS, get_check


def test_every_check_module_exposes_run():
    for name in CHECKS:
        assert callable(get_check(name).run), name


def test_browser_flags():
    assert {n for n, info in CHECKS.items() if info.browser} == {"browser", "forms", "interactions", "axe"}
