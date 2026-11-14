"""Branch records and branch-closure events.

Where the site keeps them (see the M3a notes):
- `/contact` lists branches as "### Dupaco Credit Union" / "Dupaco Branch" / address / status. The status ("Closed")
  is the live open/closed widget at crawl time (crawled around 3 a.m.), not a closure, so it is ignored (R5).
- Lobby and drive-up hours exist only in branch-opening press releases: a dateline gives the city
  ("CEDAR RAPIDS, Iowa–"), "located at ..." the address, then "**Lobby**" and "**Drive-up**" blocks of day lines.
- Closures are press releases titled like "... plans Hy-Vee branch closure" / "Dupaco closing Asbury branch ...",
  with "will close March 31, 2026" or "last day of service will be Tuesday, Oct. 1, 2024", and a sentence telling
  members which branches they can use instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional

from .pages import WORD_DATE, CleanPage, parse_date

_DAY_LINE = re.compile(r"^(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*(?:\s*[-–—]\s*(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*)?\s*:\s*\S", re.I)
_BLOCK = re.compile(r"^\*\*(Lobby|Drive-up)\*\*\s*(.*)$", re.I)
_DATELINE = re.compile(r"^([A-Z][A-Z .]+),\s+(?:Iowa|Wis\.?|Wisconsin|Ill\.?|Illinois)\s*[—–-]")
# Periods are part of addresses ("1100 E. Business Highway 151", "4615 Crosse Pointe Blvd."), so they don't end one.
_LOCATED = re.compile(r"\blocated at (\d+ [^,]+?)(?:,| at | in | with |$)", re.M)
_INTERSECTION = re.compile(r"\b(?:intersection|corner) of ([^.]+?) and ([^.,]+?)(?= in |[.,]|$)", re.M)
_PHONE = re.compile(r"\((\d{3})\)\s*(\d{3})-(\d{4})")
_ADDRESS = re.compile(r"^(\d+ [^,]+),\s*([A-Za-z .]+),\s*([A-Z]{2}),\s*(\d{5})$")   # "3299 Hillcrest Rd., Dubuque, IA, 52001"
_STREET_WORDS = {"rd", "road", "roads", "st", "street", "streets", "ave", "avenue", "dr", "drive", "ln", "lane", "blvd",
                 "boulevard", "way", "hwy", "highway", "e", "w", "n", "s", "east", "west", "north", "south", "ct", "court",
                 "pkwy"}
_GENERIC_KEYS = {"first", "main", "business", "center", "central", "point", "corridor"}
_CLOSE_TITLE = re.compile(r"\bclos(?:e|es|ing|ure)\b", re.I)
_TITLE_BRANCH = re.compile(r"((?:[A-Z][\w'-]*\s)*[A-Z][\w'-]*)\s+branch", re.I)
_CLOSE_DATE = re.compile(rf"(?:will close(?: on)?|last day of service will be(?: \w+day,)?)\s+({WORD_DATE})", re.I)
_INSTEAD = re.compile(r"\bcan (?:access|visit|use)\b", re.I)
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _street_only(address: str) -> str:
    """"3299 Hillcrest Rd., Dubuque, IA, 52001" -> "Hillcrest Rd."."""
    return re.sub(r"^\d+\s+", "", address.split(",")[0])


def _street_keys(text: str) -> set[str]:
    """Distinctive words of a street name: "3299 Hillcrest Rd." -> {"hillcrest"}, "2245 Flint Hill Dr." -> {"flint hill"}."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", text) if w.lower().strip(".") not in _STREET_WORDS]
    key = " ".join(words).lower()
    return {key} if key and key not in _GENERIC_KEYS else set()


@dataclass(frozen=True)
class Branch:
    name: str                       # "Galena", "Cedar Rapids", "Madison (3762 E. Washington Ave.)"
    city: Optional[str]
    address: Optional[str]          # as printed
    phone: Optional[str]
    lobby: tuple[str, ...]          # day lines as printed ("Monday-Thursday: 8:30 a.m.-5 p.m.")
    drive_up: tuple[str, ...]
    url: str
    published: Optional[str]        # article date for releases
    keys: frozenset[str]            # lowercase words a question may use to name it


@dataclass(frozen=True)
class Closure:
    branch: str                     # "Hy-Vee", "Asbury"
    date: str                       # ISO
    date_text: str                  # as printed: "March 31, 2026", "Oct. 1, 2024"
    last_day: bool                  # phrased as "last day of service" rather than "will close"
    instead: Optional[str]          # the "members can access ... at ..." sentence, cleaned
    url: str
    published: Optional[str]
    keys: frozenset[str]


def contact_branches(page: CleanPage) -> list[Branch]:
    lines = [l.strip() for l in page.text.split("\n") if l.strip()]
    out = []
    for i, line in enumerate(lines):
        if line != "### Dupaco Credit Union" or i + 2 >= len(lines) or lines[i + 1] != "Dupaco Branch":
            continue
        m = _ADDRESS.match(lines[i + 2])
        if not m:
            continue
        street, city = m.group(1), m.group(2).strip()
        out.append(Branch(name=city, city=city, address=lines[i + 2], phone=None, lobby=(), drive_up=(),
                          url=page.url, published=None, keys=frozenset({city.lower(), *_street_keys(street)})))
    return out


def release_branch(page: CleanPage) -> Optional[Branch]:
    """A branch-opening press release with lobby/drive-up hours, or None."""
    lines = [l.strip() for l in page.text.split("\n") if l.strip()]
    blocks: dict[str, list[str]] = {}
    current = None
    for line in lines:
        if m := _BLOCK.match(line):
            current = "lobby" if m.group(1).lower() == "lobby" else "drive_up"
            blocks[current] = [m.group(2)] if m.group(2) else []
        elif current and _DAY_LINE.match(line):
            blocks[current].append(line)
        else:
            current = None
    if not blocks:
        return None
    body = "\n".join(lines)
    city = next((m.group(1).title() for l in lines if (m := _DATELINE.match(l))), None)
    address = (m.group(1).strip() if (m := _LOCATED.search(body)) else None)
    phone = (f"({m.group(1)}) {m.group(2)}-{m.group(3)}" if (m := _PHONE.search(body)) else None)
    keys = {city.lower()} if city else set()
    if address:
        keys |= _street_keys(address)
    if m := _INTERSECTION.search(body):
        keys |= _street_keys(m.group(1)) | _street_keys(m.group(2))
    return Branch(name=city or page.title, city=city, address=address, phone=phone,
                  lobby=tuple(blocks.get("lobby", ())), drive_up=tuple(blocks.get("drive_up", ())),
                  url=page.url, published=page.published, keys=frozenset(k for k in keys if k))


def closure_event(page: CleanPage) -> Optional[Closure]:
    """A press release announcing that a named branch closes on a date, or None."""
    title = next((l.strip()[2:] for l in page.text.split("\n") if l.startswith("# ")), page.title)
    if not (_CLOSE_TITLE.search(title) and "branch" in title.lower()):
        return None
    m = _TITLE_BRANCH.search(title)
    words = [w for w in (m.group(1).split() if m else []) if w[0].isupper() and w.lower() not in ("dupaco", "plans", "closing")]
    d = _CLOSE_DATE.search(page.text)
    if not words or not d:
        return None
    branch = " ".join(words)
    instead = None
    for sentence in re.split(r"(?<=[.!?”\"])\s+", _MD_LINK.sub(r"\1", page.text)):
        if _INSTEAD.search(sentence) and "branch" in sentence.lower():
            s = re.sub(r"[“”\"]", "", sentence).strip()
            s = re.sub(r",?\s*(?:said|added) [A-Z][\w.]*(?: [A-Z][\w.]*)*\.?$", "", s).strip().rstrip(",;:—– ")
            instead = s if s.endswith(".") else s + "."
            break
    return Closure(branch=branch, date=parse_date(d.group(1)), date_text=d.group(1),
                   last_day="last day" in d.group(0).lower(), instead=instead, url=page.url,
                   published=page.published, keys=frozenset({branch.lower(), branch.lower().replace("-", " ")}))


def extract(pages: list[CleanPage]) -> tuple[list[Branch], list[Closure]]:
    branches: list[Branch] = []
    closures: list[Closure] = []
    for page in pages:
        if page.kind == "contact":
            branches += contact_branches(page)
        elif page.kind in ("press", "blog"):
            if page.kind == "press" and (b := release_branch(page)):
                branches.append(b)
            if c := closure_event(page):
                closures.append(c)
    # Name a branch by its city; add the street when a city has several (Dubuque, Madison).
    cities = [b.city for b in branches]
    branches = [b if cities.count(b.city) == 1 or not b.address
                else replace(b, name=f"{b.city} – {_street_only(b.address)}") for b in branches]
    # The same closure can be announced on a press page and a blog copy: keep the first per branch.
    seen, unique = set(), []
    for c in sorted(closures, key=lambda c: (c.branch, c.published or "", c.url)):
        if c.branch not in seen:
            seen.add(c.branch)
            unique.append(c)
    return branches, unique
