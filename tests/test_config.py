from monitor.config import load_site

def test_cap_finisher_profile_loads():
    site = load_site("cap-finisher")
    assert site["base_url"] == "https://cap-finisher.com"
    assert "/mitglieder.html" in site["paths"]
    assert site["checks"]["browser"]["enabled"] is True
