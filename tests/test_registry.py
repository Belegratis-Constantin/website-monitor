from monitor.checks import available_checks

def test_expected_modules_registered():
    expected = {"uptime", "links", "assets", "headers", "seo", "content", "ssl", "accessibility", "browser"}
    assert expected.issubset(set(available_checks()))
