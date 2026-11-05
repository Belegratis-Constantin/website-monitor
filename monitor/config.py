from pathlib import Path
import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "sites"

def load_site(name: str) -> dict:
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Site profile not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    base_url = data.get("base_url", "").rstrip("/")
    if not base_url.startswith(("http://", "https://")) or "REPLACE-WITH" in base_url:
        raise ValueError(f"Set a valid base_url in {path}")
    data["base_url"] = base_url
    data.setdefault("paths", ["/"])
    data.setdefault("checks", {})
    data.setdefault("timeout_seconds", 20)
    data.setdefault("user_agent", "WebsiteMonitor/1.0")
    return data
