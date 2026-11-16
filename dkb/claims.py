"""Company facts: dated claims from press releases, plus a few undated site-wide values.

Dated claims (R3: the newest dated article wins; page counts never decide):
- Every press release ends with an "ABOUT DUPACO COMMUNITY CREDIT UNION" paragraph that restates the branch count,
  membership, assets, employees and service area as of its publish date. Old releases keep their old numbers
  ("24 branch office locations"), so the newest article wins, not the most common value (23 is on more pages than 22).
  Claims are read only from that paragraph: article bodies quote other organizations' numbers ("over 200 employees").
- The Forbes count comes from the Forbes releases themselves ("This was the fifth time Forbes recognized Dupaco.").

Undated site values: the routing number ("routing number (273974581)", "ABA number (...)") and the main phone
(the most widely printed toll-free number), both copied as printed; and the membership conditions on the join page.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from .pages import CleanPage

_ABOUT = re.compile(r"^\**ABOUT DUPACO COMMUNITY CREDIT UNION\**:?\**\s*(.*)$", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")

# topic -> pattern on a sentence of the About paragraph; group 1 is the value as printed.
BOILERPLATE = {
    "branch_count": re.compile(r"\b(\d+) branch office locations\b"),
    "employees": re.compile(r"\b((?:over|more than) [\d,]+) employees\b"),
    "members": re.compile(r"\bmembership has grown to (more than [\d,]+)"),
    "assets": re.compile(r"\bassets exceeding (\$[\d.,]+ (?:billion|million))"),
    "service_area": re.compile(r"\bserves residents in (\d+ counties throughout [^.]+)"),
    "recognition": re.compile(r"^It is (?:an? )?(.*Forbes.*?)\.?$"),
}
_ORDINAL = r"(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
FORBES_COUNT = [
    re.compile(rf"\b(?:the )?{_ORDINAL} time Forbes (?:has )?recognized Dupaco\b", re.I),
    re.compile(rf"\bFor a {_ORDINAL} (?:year|time) in a row, Forbes\b", re.I),
]
_ROUTING = re.compile(r"\b(?:routing|ABA) number\b[^0-9\n]{0,40}?(\d{9})\b", re.I)
_TOLL_FREE = re.compile(r"\b8(?:00|33|44|55|66|77|88)-\d{3}-\d{4}\b")


@dataclass(frozen=True)
class Claim:
    topic: str              # branch_count, employees, members, assets, service_area, recognition, forbes_count
    value: str              # as printed: "22", "more than 179,000", "$3.6 billion", "fifth"
    sentence: str           # the sentence it came from (used as the quote)
    url: str
    published: str          # ISO article date


@dataclass(frozen=True)
class SiteValue:
    topic: str              # routing_number, phone
    value: str
    pages: tuple[tuple[str, str, str], ...]   # (kind, url, sentence) for every page that prints it


@dataclass(frozen=True)
class Membership:
    url: str
    conditions: tuple[str, ...]   # the "WHO CAN JOIN" bullets, as printed (links reduced to their text)
    how: Optional[str]            # "To join Dupaco, simply open a savings account with $25 or more."
    states: Optional[str]         # "Iowa, Illinois and Wisconsin" (from "You can find us in ...")


def _plain(s: str) -> str:
    return re.sub(r"\s+", " ", _MD_LINK.sub(r"\1", s).replace(" ", " ").replace("\xa0", " ")).strip()


def _about_paragraph(text: str) -> Optional[str]:
    """The About paragraph: either on the heading line itself or the next non-empty line."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = _ABOUT.match(line.strip())
        if not m:
            continue
        if m.group(1).strip():
            return m.group(1)
        return next((l for l in lines[i + 1:] if l.strip()), None)
    return None


def article_claims(page: CleanPage) -> list[Claim]:
    if page.kind not in ("press", "blog") or not page.published:
        return []
    out = []
    if para := _about_paragraph(page.text):
        for sentence in _SENTENCE_SPLIT.split(_plain(para)):
            for topic, rx in BOILERPLATE.items():
                if m := rx.search(sentence):
                    out.append(Claim(topic, m.group(1).strip(), sentence, page.url, page.published))
    # Only the Forbes releases themselves, and never a related-article card: cards quote an old release's first line,
    # cut off with "..." ("DUBUQUE, Iowa—For a third year in a row, Forbes named ... in its ranking of..."), which is
    # that old article's claim, not this page's.
    title = next((l[2:] for l in page.text.split("\n") if l.startswith("# ")), page.title)
    if "forbes" in title.lower():
        for line in page.text.split("\n"):
            if line.rstrip().endswith(("...", "…")) or line.startswith("Read More"):
                continue
            for sentence in _SENTENCE_SPLIT.split(_plain(line)):
                if m := next((m for rx in FORBES_COUNT if (m := rx.search(sentence))), None):
                    out.append(Claim("forbes_count", m.group(1).lower(), sentence, page.url, page.published))
    return out


def site_values(pages: list[CleanPage]) -> dict[str, SiteValue]:
    """Undated values printed across the site. The value most pages print wins; every page printing it is kept."""
    found: dict[str, dict[str, list]] = {"routing_number": {}, "phone": {}}
    for page in pages:
        if page.kind == "junk":
            continue
        for topic, rx in (("routing_number", _ROUTING), ("phone", _TOLL_FREE)):
            seen = set()
            for line in page.text.split("\n"):
                for m in rx.finditer(line):
                    value = m.group(1) if rx.groups else m.group(0)
                    if value not in seen:
                        seen.add(value)
                        found[topic].setdefault(value, []).append((page.kind, page.url, _plain(line)))
    out = {}
    for topic, by_value in found.items():
        if by_value:
            value = max(sorted(by_value), key=lambda v: len(by_value[v]))
            out[topic] = SiteValue(topic, value, tuple(sorted(by_value[value], key=lambda t: t[1])))
    return out


def membership(pages: list[CleanPage]) -> Optional[Membership]:
    """The join page's "WHO CAN JOIN" section: its conditions, its "To join ..." sentence and its states line.
    Two layouts: bullets ("- You live or work within our charter area.", 04-05) or one sentence ("You live or work
    within our charter area or have an immediate family member who's a member.", 01-03)."""
    for page in sorted(pages, key=lambda p: p.url):
        lines = [l.strip() for l in page.text.split("\n")]
        start = next((i for i, l in enumerate(lines) if re.fullmatch(r"#+\s*WHO CAN JOIN", l, re.I)), None)
        if start is None:
            continue
        section = []                                    # content lines up to the next heading after the content
        for line in lines[start + 1:]:
            if line.startswith("#"):
                if section:
                    break
                continue
            if line and not line.startswith("["):       # skip blank lines and button links
                section.append(_plain(line))
        bullets = [l[2:].rstrip(".") for l in section if l.startswith("- ")]
        sentences = [s for l in section if not l.startswith("- ") for s in _SENTENCE_SPLIT.split(l)]
        conditions = bullets or [s.rstrip(".") for s in sentences[:1] if s.startswith("You ")]
        how = next((s for s in sentences if re.match(r"To join\b", s)), None)
        text = _plain(page.text.replace("\n", " "))
        states = m.group(1) if (m := re.search(r"You can find us in ([^.]+)\.", text)) else None
        if conditions:
            return Membership(page.url, tuple(conditions), how, states)
    return None


def current(claims: list[Claim], topic: str, ref_date: str) -> tuple[Optional[Claim], list[Claim], list[Claim]]:
    """(winner, agreeing, older values) for a topic as of ref_date. R3: the newest dated article wins; on the same
    date the value more articles print wins. `older` holds one claim per losing value (its newest article)."""
    dated = [c for c in claims if c.topic == topic and c.published <= ref_date]
    if not dated:
        return None, [], []
    newest = max(c.published for c in dated)
    counts = Counter(c.value for c in dated if c.published == newest)
    value = max(sorted(counts), key=lambda v: counts[v])
    ordered = sorted(dated, key=lambda c: (c.published, c.url), reverse=True)
    winner = next(c for c in ordered if c.value == value and c.published == newest)
    agreeing = [c for c in ordered if c.value == value and c is not winner]
    older, seen = [], {value}
    for c in ordered:
        if c.value not in seen:
            seen.add(c.value)
            older.append(c)
    return winner, agreeing, older
