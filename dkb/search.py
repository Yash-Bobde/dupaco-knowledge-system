"""Search the site's own sentences (open-ended questions no template covers).

Each cleaned page is split into passages: a heading with the paragraphs under it (tables, images and bare
call-to-action links left out; rate tables already have their own answers). Passages are ranked with BM25
(own code, no package) and then weighted by how authoritative the page is (product pages over blog posts).

Stale suppression: a passage that states a rate value the truth table marks as losing (e.g. 5.34% after the rate
sheet moved to 5.39%) is dropped, so old numbers can't reach an answer through search.

A question word can match through a small list of synonyms ("university" also finds "college"), and the
question's information is measured per concept, so a rare synonym can't dominate the refusal gate.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from .pages import CleanPage

# Question/passage words that carry no meaning for matching. Includes the polite filler of real chat logs.
STOP = frozenset("""
a an the and or but if of to in on at by for with from as is are was were be been being am do does did doing have has
had having i me my mine myself we us our ours you your yours it its this that these those there here what which who
whom whose when where why how can could would should will shall may might must any some all each every not no nor
dupaco credit union cu cus please tell know want need get got just like also about into than then so too very im id
ive ill youre dont doesnt isnt arent cant wont one more most much many way ways thing things anything something
let help hi hello thanks thank again asking ask repeat say said give show find going go okay ok really still yes
""".split())

# Words members use for the same thing (stemmed at import). Kept small and banking-specific.
_SYNONYM_GROUPS = [
    "university college school tuition", "car auto vehicle", "cd certificate", "phone mobile smartphone cellphone",
    "kid kids child children teen teenager youth minor", "house home", "fee fees charge cost", "lost stolen missing",
    "business company commercial", "vto volunteer", "senior elderly retiree", "grant assistance",
    "cancel close", "lend loan borrow", "wallet pay",
]

_WORD = re.compile(r"[a-z0-9]+(?:[$'][a-z0-9]+)*")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


@lru_cache(maxsize=None)
def _stem(w: str) -> str:
    if w.endswith("'s"):
        w = w[:-2]
    w = w.replace("'", "")
    if not w.isalpha():
        return w
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    return w[:6]          # crude prefix stem: flexible / flexibility -> "flexib"


def terms(text: str) -> list[str]:
    """Lowercased, stemmed content words (stop words dropped)."""
    out = []
    for w in _WORD.findall(text.lower().replace("’", "'").replace("‘", "'")):
        if len(w) < 2 or w in STOP or w.replace("'", "") in STOP:
            continue
        s = _stem(w)
        if s not in STOP:
            out.append(s)
    return out


SYNONYMS: dict[str, frozenset] = {}
for _group in _SYNONYM_GROUPS:
    _stems = frozenset(_stem(w) for w in _group.split())
    for _s in _stems:
        SYNONYMS[_s] = SYNONYMS.get(_s, frozenset()) | _stems


def concepts(text: str) -> list[frozenset]:
    """The question's concepts: each content word with its synonyms, in order, without repeats."""
    out: list[frozenset] = []
    for t in terms(text):
        c = SYNONYMS.get(t, frozenset((t,)))
        if c not in out:
            out.append(c)
    return out


# ---------------------------------------------------------------- passages

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_LINK_ONLY = re.compile(r"^(?:[-*]\s*)?(?:\[[^\]]*\]\([^)]*\)\s*)+$")
_HEADING = re.compile(r"^(#{1,6})\s*(.*)$")
_DECOR = re.compile(r"\*\*|__|(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)|\\(?=[\\|*_#\[\]()$-])")
_DATELINE = re.compile(r"^[A-Z][A-Za-z. ]{1,30},\s*[A-Z][A-Za-z.]{1,12}\s*[—–]+\s*")


def plain(md: str) -> str:
    """Markdown -> readable text: images dropped, links -> their text, emphasis/escapes and datelines removed."""
    s = _IMAGE.sub("", md)
    s = _LINK.sub(lambda m: m.group(1), s)
    s = _DECOR.sub("", s)
    s = re.sub(r"^\s*(?:[-*>]\s+|\d+\.\s+)", "", s)
    s = _DATELINE.sub("", s.strip())
    return re.sub(r"\s+", " ", s).strip()


@dataclass(frozen=True)
class Passage:
    url: str
    path: str
    kind: str
    title: str
    heading: str
    text: str                    # plain text, one line per paragraph or list item (what an answer quotes)
    published: Optional[str]
    pos: int                     # order on the page


MAX_CHARS = 700


@lru_cache(maxsize=None)
def _sections(text: str) -> tuple[tuple[str, str], ...]:
    """(heading, plain text) chunks of a cleaned page. Cached by page text: most pages repeat across snapshots."""
    out: list[tuple[str, str]] = []
    heading, blocks, block = "", [], []

    def close_block():
        if block:
            blocks.append("\n".join(block))
            block.clear()

    def close_section():
        close_block()
        chunk, size = [], 0
        for b in blocks:
            if chunk and size + len(b) > MAX_CHARS:
                out.append((heading, "\n".join(chunk)))
                chunk, size = [], 0
            chunk.append(b)
            size += len(b)
        if chunk:
            out.append((heading, "\n".join(chunk)))
        blocks.clear()

    for line in text.split("\n"):
        s = line.strip()
        if m := _HEADING.match(s):
            close_section()
            heading = plain(m.group(2))
            continue
        if not s:
            close_block()
            continue
        if s.startswith("|") or s in ("* * *", "---", "***") or _LINK_ONLY.match(s):
            close_block()
            continue
        if p := plain(s):
            block.append(p)
    close_section()
    return tuple((h, t) for h, t in out if len(t) >= 25)


def passages(page: CleanPage) -> list[Passage]:
    if page.kind == "junk":
        return []
    return [Passage(page.url, page.path, page.kind, page.title, h, t, page.published, i)
            for i, (h, t) in enumerate(_sections(page.text))]


@lru_cache(maxsize=None)
def _tf(text: str) -> tuple[tuple[str, int], ...]:
    tf: dict[str, int] = {}
    for w in terms(text):
        tf[w] = tf.get(w, 0) + 1
    return tuple(tf.items())


# ---------------------------------------------------------------- the index

# How much a page kind is trusted for a member answer (multiplies the BM25 score).
AUTHORITY = {"product": 1.35, "rate_sheet": 1.2, "contact": 1.25, "home": 1.15, "other": 1.15, "landing": 1.0,
             "press": 0.9, "blog": 0.8, "calculator": 0.6, "file": 0.6, "junk": 0.0}
K1, B = 1.2, 0.75


@dataclass
class Hit:
    passage: Passage
    score: float
    matched: frozenset          # question terms found in the passage text, its heading or its page title
    also: tuple                 # other pages carrying the same passage text


class Index:
    """BM25 over one snapshot's passages; identical passage texts are indexed once (best page kept)."""

    def __init__(self, pages: list[CleanPage], stale: set[float]) -> None:
        by_text: dict[str, list[Passage]] = {}
        for page in pages:
            for p in passages(page):
                if any(round(float(v), 3) in stale for v in _PERCENT.findall(p.text)):
                    continue                                    # states a losing rate value: never searchable
                by_text.setdefault(p.text, []).append(p)
        self.docs: list[Passage] = []
        self.also: list[tuple] = []
        for copies in by_text.values():
            copies.sort(key=lambda p: (-AUTHORITY.get(p.kind, 1.0), len(p.path), p.url))
            self.docs.append(copies[0])
            self.also.append(tuple(dict.fromkeys(c.url for c in copies[1:] if c.url != copies[0].url)))
        self.tf: list[dict[str, int]] = []
        self.context: list[frozenset] = []                  # heading + title words
        self.postings: dict[str, list[int]] = {}
        self.lengths: list[int] = []
        for i, p in enumerate(self.docs):
            tf = dict(_tf(p.text))
            for w, n in _tf(p.heading):
                tf[w] = tf.get(w, 0) + n
            self.tf.append(tf)
            self.context.append(frozenset(dict(_tf(p.heading))) | frozenset(dict(_tf(p.title))))
            self.lengths.append(sum(tf.values()) or 1)
            for w in tf:
                self.postings.setdefault(w, []).append(i)
        self.avg = sum(self.lengths) / len(self.lengths) if self.lengths else 1.0
        self.n = len(self.docs)

    def idf(self, term: str) -> float:
        df = len(self.postings.get(term, ()))
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def concept_idf(self, c: frozenset) -> float:
        """A concept is as informative as its most common wording."""
        return min(self.idf(t) for t in c)

    def search(self, cs: list[frozenset], k: int = 10) -> list[Hit]:
        q = list(dict.fromkeys(t for c in cs for t in sorted(c)))
        scores: dict[int, float] = {}
        for t in q:
            idf = self.idf(t)
            for i in self.postings.get(t, ()):
                f = self.tf[i][t]
                norm = f * (K1 + 1) / (f + K1 * (1 - B + B * self.lengths[i] / self.avg))
                scores[i] = scores.get(i, 0.0) + idf * norm
        # title words the passage itself lacks still count a little ("Student Loans & Refinancing")
        for i in list(scores):
            extra = sum(0.5 * self.idf(t) for t in q if t in self.context[i] and t not in self.tf[i])
            scores[i] = (scores[i] + extra) * AUTHORITY.get(self.docs[i].kind, 1.0)
        best = sorted(scores, key=lambda i: (-scores[i], self.docs[i].url, i))[:k]
        return [Hit(self.docs[i], scores[i], frozenset(t for t in q if t in self.tf[i] or t in self.context[i]),
                    self.also[i]) for i in best]

    def coverage(self, cs: list[frozenset], matched) -> float:
        """Share of the question's information (idf-weighted, per concept) that a passage covers."""
        total = sum(self.concept_idf(c) for c in cs)
        return sum(self.concept_idf(c) for c in cs if c & matched) / total if total else 0.0
