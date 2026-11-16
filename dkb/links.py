"""Links the site itself uses: anchor text -> target, counted across pages.

A link answer gives the target the site's own buttons point to most ("Compare rates" -> /personal/savings-account#rates,
"Find a location" -> /contact, "Join Dupaco" -> join.dupaco.com), never a URL we compose. Only live targets count:
a www.dupaco.com target must be a page crawled in the same snapshot (old links to removed pages such as
/connect/branch-atm-locations/ are dead), other dupaco.com hosts (join., shine., homeloans.) are the site's own
apps, and anything else (the dev host live-dupaco.pantheonsite.io, HubSpot preview hosts) is ignored.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlsplit

from .pages import CleanPage, url_path

_LINK = re.compile(r"(?<!!)\[([^\]\n]{1,100})\]\((https?://[^)\s]+)\)")
_SIMPLE_FRAGMENT = re.compile(r"^[a-z]+(?:-[a-z]+)*$")    # "#rates", "#locator"; not "#dupaco-locator-widget_1769..."

LOCATOR_ANCHOR = re.compile(r"\b(?:find a (?:branch|location)|nearest branch|branch or atm|atms?|locator|visit us|"
                            r"shared branches)\b", re.I)
JOIN_ANCHOR = re.compile(r"\b(?:join dupaco|become a member)\b", re.I)
SHINE_ANCHOR = re.compile(r"\b(?:shine online|online banking)\b", re.I)
RATES_ANCHOR = re.compile(r"\brates?\b", re.I)


@dataclass(frozen=True)
class Link:
    text: str        # anchor text as printed
    url: str         # target as printed
    host: str        # lowercased host
    path: str        # normalized path (url_path)
    fragment: str
    page: str        # url of the page that holds the link


def page_links(page: CleanPage) -> list[Link]:
    if page.kind == "junk":
        return []
    out = []
    for m in _LINK.finditer(page.text):
        url = m.group(2)
        parts = urlsplit(url)
        out.append(Link(m.group(1).strip(), url, parts.netloc.lower(), url_path(url), parts.fragment, page.url))
    return out


def _live(link: Link, live_paths: set[str]) -> bool:
    if link.host in ("www.dupaco.com", "dupaco.com"):
        return link.path in live_paths
    return link.host.endswith(".dupaco.com")


def _display(host: str, path: str, links: list[Link]) -> str:
    """The target as a member should see it: scheme + host + path as printed, plus a fragment only when the site
    most often links with a readable one (#rates); generated widget ids are dropped (they still land on the page)."""
    printed = Counter(urlsplit(l.url)._replace(query="", fragment="").geturl() for l in links)
    base = max(sorted(printed), key=lambda u: printed[u])
    if host in ("www.dupaco.com", "dupaco.com"):
        base = base.rstrip("/") if path != "/" else base
    fragments = Counter(l.fragment if _SIMPLE_FRAGMENT.match(l.fragment) else "" for l in links)
    top = max(sorted(fragments), key=lambda f: fragments[f])
    return base + (f"#{top}" if top else "")


def best_target(links: Iterable[Link], live_paths: set[str], anchor: re.Pattern, *,
                paths: Optional[set[str]] = None, prefer_offsite: bool = False) -> Optional[tuple[str, list[Link]]]:
    """The live target that the most links with matching anchor text point to: (display url, those links).
    `paths` limits targets to those pages (e.g. the product pages holding today's certificate rates; in-page links
    such as "today's rates" -> #rates count too, they show where the rates sit). `prefer_offsite` ranks the site's
    own apps (join.dupaco.com) before www pages that only describe them (the online application is the action)."""
    groups: dict[tuple[str, str], list[Link]] = {}
    for link in links:
        if not anchor.search(link.text) or not _live(link, live_paths):
            continue
        if paths is not None and (link.host not in ("www.dupaco.com", "dupaco.com") or link.path not in paths):
            continue
        groups.setdefault((link.host, link.path), []).append(link)
    if not groups:
        return None
    def rank(key):
        host, path = key
        offsite = host not in ("www.dupaco.com", "dupaco.com")
        return (-(offsite and prefer_offsite), -len({l.page for l in groups[key]}), -len(groups[key]), key)
    host, path = min(groups, key=rank)
    return _display(host, path, groups[(host, path)]), groups[(host, path)]
