"""Link answers: the URL the site's own buttons use for a product's rates, the branch/ATM locator, joining, and
online banking (see links.py). Every URL in an answer is a link target printed on the site.

Rates links: the candidate pages are the product pages that hold today's winning rates for that family (so a page
that only keeps a stale copy, e.g. /personal/checking-accounts with an old auto table, is never offered); the site's
"Compare rates" / "today's rates" links then pick among them and give the #fragment. With no such links, the product
page holding the most current rows is used, then the rate sheet.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Optional
from urllib.parse import urlsplit

from . import links as L
from .pages import url_path
from .question import Question
from .reply import LINK_WORDS
from .shapes import Answer, Source
from .store import SnapshotData
from .truth import matching, pick

WHERE_FIND = re.compile(r"\bwhere (?:can|do|should) i (?:find|see|check|look up|look|log ?in|sign in|go to)\b")
LOCATOR_Q = re.compile(r"\b(?:locator|branch(?:es)?|atms?|locations?)\b")
JOIN_Q = re.compile(r"\b(?:join|become a member|membership|sign up)\b")
SHINE_Q = re.compile(r"\b(?:online banking|shine|log ?in|sign in)\b")

FAMILY_LABEL = {"certificate": "certificate (CD)", "auto": "auto loan", "credit_card": "credit card",
                "money_market": "money market", "ira": "IRA", "hsa": "health savings account",
                "heloc": "home equity line of credit", "home_equity_loan": "home equity loan",
                "home_improvement": "home improvement loan", "personal_loan": "personal loan",
                "boat_rv": "boat and RV loan", "atv": "ATV, snowmobile and jet ski loan"}


def _page_url(url: str) -> str:
    return urlsplit(url)._replace(query="", fragment="").geturl().rstrip("/")


def _sources(target: str, found: list, snap: SnapshotData, limit: int = 5) -> list[Source]:
    """The target first, then the pages whose links point to it (quote = the anchor text used there)."""
    titles = {p.path: p.title for p in snap.pages.values()}
    on_site = urlsplit(target).netloc.lower() in ("www.dupaco.com", "dupaco.com")
    out = [Source(url=target, snapshot_id=snap.snapshot_id, quote=titles.get(url_path(target)) if on_site else None)]
    anchors: dict[str, str] = {}
    for link in sorted(found, key=lambda l: (l.page, l.text)):
        anchors.setdefault(link.page, f"[{link.text}]({link.url})")
    for page, quote in anchors.items():
        if len(out) == limit:
            break
        if _page_url(page) != _page_url(target):
            out.append(Source(url=page, snapshot_id=snap.snapshot_id, quote=quote))
    return out


def rates_link(family: str, segment: str, snap: SnapshotData) -> Optional[tuple[str, list]]:
    """(url, supporting links) for where the site shows today's rates of a family."""
    by_key: dict[tuple, list] = defaultdict(list)
    for f in matching(snap.facts, family=family, segment=segment):
        by_key[f.key].append(f)
    rows: Counter = Counter()
    sheet = None
    for facts in by_key.values():
        p = pick(facts)
        for f in (p.winner, *p.support):
            if f.page_kind == "product":
                rows[(f.path, _page_url(f.url))] += 1
            elif f.page_kind == "rate_sheet" and sheet is None:
                sheet = _page_url(f.url)
    if rows:
        found = L.best_target(snap.links, snap.live_paths, L.RATES_ANCHOR, paths={path for path, _ in rows})
        if found:
            return found
        (_, url), _ = max(sorted(rows.items()), key=lambda kv: kv[1])
        return url, []
    return (sheet, []) if sheet else None


def link_answer(q: Question, snap: SnapshotData) -> Optional[Answer]:
    text = q.text
    if not (LINK_WORDS.search(text) or WHERE_FIND.search(text)):
        return None
    if q.families and q.rate_words:
        family = q.families[0]
        found = rates_link(family, q.segment or "personal", snap)
        if not found:
            return None
        url, used = found
        label = FAMILY_LABEL.get(family, family.replace("_", " "))
        business = "business " if q.segment == "business" else ""
        return Answer(text=f"You can see Dupaco's current {business}{label} rates at {url}.",
                      sources=_sources(url, used, snap), confident=True)
    for rx, anchor, offsite, template in (
            (LOCATOR_Q, L.LOCATOR_ANCHOR, False, "Use Dupaco's branch and ATM locator at {url}."),
            (JOIN_Q, L.JOIN_ANCHOR, True, "You can join Dupaco online at {url}."),
            (SHINE_Q, L.SHINE_ANCHOR, True, "Log in to Shine Online Banking at {url}.")):
        if rx.search(text) and (found := L.best_target(snap.links, snap.live_paths, anchor, prefer_offsite=offsite)):
            url, used = found
            return Answer(text=template.format(url=url), sources=_sources(url, used, snap), confident=True)
    return None
