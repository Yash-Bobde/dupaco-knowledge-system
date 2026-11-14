"""Clean a crawled page, tag what kind of page it is, and read the dates printed on it.

The crawler already removed the site-wide menus and footers. What is left is page-level widget noise,
removed here by explicit patterns (not by "lines that repeat across pages": repeated date lines come from
related-article cards, and a frequency filter would either keep them or delete real dates).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional
from urllib.parse import urlsplit

# ---------------------------------------------------------------- dates

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}  # keyed by the first 3 letters
_MONTH_WORD = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
               r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
NUMERIC_DATE = r"\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})"
WORD_DATE = _MONTH_WORD + r"\.?\s+\d{1,2},\s+\d{4}"

_NUMERIC_RE = re.compile(rf"\b(\d{{1,2}})/(\d{{1,2}})/(\d{{4}}|\d{{2}})\b")
_WORD_RE = re.compile(rf"\b({_MONTH_WORD})\.?\s+(\d{{1,2}}),\s+(\d{{4}})\b", re.I)
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _iso(y: int, m: int, d: int) -> Optional[str]:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def parse_date(s: str) -> Optional[str]:
    """First date in `s` as an ISO string: 5/1/2026, 08/13/25, June 20, 2025, Sept. 9, 2025, 2026-05-12."""
    candidates = []
    if m := _NUMERIC_RE.search(s):
        y = int(m.group(3))
        candidates.append((m.start(), _iso(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2)))))
    if m := _WORD_RE.search(s):
        candidates.append((m.start(), _iso(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))))
    if m := _ISO_RE.search(s):
        candidates.append((m.start(), _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
    found = sorted(c for c in candidates if c[1])
    return found[0][1] if found else None


# "Rates are effective as of 06/15/2026", "**Rates are effective as of 03/03/2026 …**", "Rates effective 01/01/2023",
# "Rate effective as of 03/02/2026". Prose such as "…standard APR, which effective 1/1/2026 is…" is not a table stamp.
EFFECTIVE_RE = re.compile(rf"\bRates?\s+(?:are\s+)?effective\s+(?:as\s+of\s+)?({NUMERIC_DATE})", re.I)


def effective_date(line: str) -> Optional[str]:
    """ISO date of a "Rates are effective as of …" stamp in this line, else None."""
    m = EFFECTIVE_RE.search(line)
    return parse_date(m.group(1)) if m else None


# ---------------------------------------------------------------- page kinds

# Ordered: the first matching rule wins. Paths are lowercased, without host, query or trailing slash.
_KIND_RULES = [
    ("junk", re.compile(r"^/privacy-policy$"), "containerType=MODAL"),  # privacy-policy modal copies
    ("rate_sheet", re.compile(r"^/(?:rates|business-rates)$"), None),
    ("contact", re.compile(r"^/contact$"), None),
    ("press", re.compile(r"^/newsroom(?:/|$)"), None),
    ("blog", re.compile(r"^/(?:learn|business/resources|dupacodaily|\d{4}/\d{2})(?:/|$)"), None),
    ("calculator", re.compile(r"^/(?:calculators|business-calculators)(?:/|$)"), None),
    ("file", re.compile(r"^/hubfs/"), None),
    # Marketing pages that repeat rate tables (checked by title, 2026-11-14): *-lp promos, switch-and-earn,
    # "Give Your Savings More Momentum" (/earn-more), the mortgage guidance and refinance pitches, /checking.
    ("landing", re.compile(r"(?:-lp$|^/(?:switch-and-earn|earn-more|checking|homebuying-guidance|mortgage-refi)$)"), None),
    ("product", re.compile(r"^/(?:personal|business|savings-account|savings)(?:/|$)"), None),  # /savings*: Bright Savings
]


def url_path(url: str) -> str:
    """'https://www.dupaco.com/Personal/Loans/?hsLang=en#rates' -> '/personal/loans' ('/' for the home page)."""
    path = urlsplit(url).path.rstrip("/").lower()
    return path or "/"


def page_kind(url: str) -> str:
    """What kind of page this is, judged from its URL. Rate tables on the page do not change it: calculators and
    landing pages carry rate tables too (often the stale ones), so authority stays with the page kind and
    freshness with the table's effective date."""
    path, query = url_path(url), urlsplit(url).query
    for kind, path_re, query_part in _KIND_RULES:
        if path_re.search(path) and (query_part is None or query_part in query):
            return kind
    return "home" if path == "/" else "other"


# ---------------------------------------------------------------- cleaning

_FLAG_LINE = re.compile(r"^\s*- [\U0001F1E6-\U0001F1FF]{2}")          # phone-country dropdown: "- 🇵🇰 Pakistan +92"
_HANDPICKED = re.compile(r"^(#{1,6})\s*Handpicked for You\s*$", re.I)   # related-article cards (other articles' dates)
_HEADING = re.compile(r"^(#{1,6})\s")
_NOISE_LINE = re.compile(
    r"^(?:✕|Previous|Next|Load More|#### Filter|Sort By:.*|- \d{1,2}|"
    r"\*\*Heads up! This link leads to a different website\.\*\*|"
    r"We only do this when it's helpful for you\. But we must inform you that Dupaco isn't responsible.*|"
    r"\[Continue\]\([^)]*\) Cancel)$"
)
_ESCAPED_NEWLINES = re.compile(r"(?:\\{1,2}r\\{1,2}n)+")               # literal "\\r\\n" left inside footnotes


def clean_markdown(markdown: str) -> str:
    """Remove widget noise; keep the content (and its wording) exactly as crawled."""
    text = _ESCAPED_NEWLINES.sub("\n", markdown.replace("\r\n", "\n"))
    out: list[str] = []
    skip_level = 0  # inside a "Handpicked for You" section: its heading level, else 0
    for line in text.split("\n"):
        s = line.strip()
        heading = _HEADING.match(s)
        if skip_level:
            if heading and len(heading.group(1)) <= skip_level:
                skip_level = 0  # a heading at the same or a higher level ends the cards section
            else:
                continue
        if m := _HANDPICKED.match(s):
            skip_level = len(m.group(1))
            continue
        if _FLAG_LINE.match(s) or _NOISE_LINE.match(s):
            continue
        out.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


# ---------------------------------------------------------------- publish date

_AUTHOR_LINE = re.compile(r"^\[[^\]]+\]\([^)]*/author/[^)]*\)$")
_DATE_LINE = re.compile(rf"^{WORD_DATE}$", re.I)
_WRITTEN_BY = re.compile(r"^Written by \[[^\]]+\]\([^)]*/author/[^)]*\)\s*\\?\|\s*(.+)$")


def publish_date(text: str) -> Optional[str]:
    """Article date of a press release or blog post. Two layouts exist near the top of the page:
      A) author link on one line, the date on the next ("June 20, 2025"), above the title;
      B) "Written by [Author](.../author/...) \\| Jan 22, 2021 8:15:00 PM" under the title.
    Dates further down belong to other things (related-article cards are already removed by clean_markdown)."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for i, line in enumerate(lines[:12]):
        if m := _WRITTEN_BY.match(line):
            return parse_date(m.group(1))
        if _AUTHOR_LINE.match(line) and i + 1 < len(lines) and _DATE_LINE.match(lines[i + 1]):
            return parse_date(lines[i + 1])
    return None


# ---------------------------------------------------------------- the cleaned page

@dataclass(frozen=True)
class CleanPage:
    url: str                     # as crawled
    path: str                    # lowercased path, no host/query/trailing slash
    title: str
    kind: str                    # rate_sheet, product, landing, calculator, contact, press, blog, file, junk, home, other
    text: str                    # cleaned markdown
    published: Optional[str]     # ISO article date (press/blog), else None


def clean_page(url: str, title: str, markdown: str) -> CleanPage:
    kind = page_kind(url)
    text = clean_markdown(markdown)
    published = publish_date(text) if kind in ("press", "blog") else None
    return CleanPage(url=url, path=url_path(url), title=title, kind=kind, text=text, published=published)
