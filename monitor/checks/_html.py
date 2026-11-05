"""HTML helpers shared by the crawler-style checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin

SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "data:", "blob:", "sms:", "#")

# (tag, attribute, kind)
RESOURCE_ATTRS = [
    ("a", "href", "link"),
    ("link", "href", "resource"),
    ("script", "src", "script"),
    ("img", "src", "image"),
    ("source", "src", "media"),
    ("video", "poster", "image"),
    ("iframe", "src", "frame"),
]

CSS_URL = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""")


@dataclass(frozen=True)
class Ref:
    url: str          # absolute, without fragment
    fragment: str     # '' when none
    kind: str         # link | resource | script | image | media | frame
    raw: str


def _srcset(value: str) -> list[str]:
    return [part.strip().split()[0] for part in value.split(",") if part.strip()]


def extract_refs(soup, page_url: str) -> list[Ref]:
    refs: list[Ref] = []

    def add(raw: str | None, kind: str) -> None:
        if not raw:
            return
        raw = raw.strip()
        if not raw or raw.lower().startswith(SKIP_SCHEMES):
            return
        absolute, fragment = urldefrag(urljoin(page_url, raw))
        if absolute.startswith(("http://", "https://")):
            refs.append(Ref(absolute, fragment, kind, raw))

    for tag, attr, kind in RESOURCE_ATTRS:
        for el in soup.find_all(tag):
            if tag == "link":
                rel = {r.lower() for r in el.get("rel", [])}
                # preconnect/dns-prefetch point at origins, not fetchable documents
                if rel & {"preconnect", "dns-prefetch"}:
                    continue
                kind = "link" if rel & {"canonical", "alternate"} else "resource"
            add(el.get(attr), kind)
    for el in soup.find_all(["img", "source"]):
        for candidate in _srcset(el.get("srcset", "")):
            add(candidate, "image")
    for el in soup.find_all(style=True):
        for match in CSS_URL.findall(el["style"]):
            add(match, "image")
    return refs


def css_refs(css_text: str, css_url: str) -> list[str]:
    out = []
    for raw in CSS_URL.findall(css_text):
        if raw.lower().startswith(("data:", "#")):
            continue
        out.append(urldefrag(urljoin(css_url, raw.strip()))[0])
    for raw in re.findall(r"""@import\s+['"]([^'"]+)['"]""", css_text):
        out.append(urljoin(css_url, raw))
    return out


def element_ids(soup) -> set[str]:
    ids = {el["id"] for el in soup.find_all(attrs={"id": True})}
    ids |= {el["name"] for el in soup.find_all("a", attrs={"name": True})}
    return ids
