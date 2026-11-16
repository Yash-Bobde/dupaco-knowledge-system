"""Open-ended answers from the site's own sentences, for questions no template covers.

No key: quote the best sentence (plus the one after it when it continues the thought), verbatim, and cite its page.
With OPENAI_API_KEY: the LLM writes 1-3 sentences from the top passages only (llm.py); if it fails or states a
number the passages don't contain, the quoted answer (or a refusal) is used instead; NOT_FOUND means refuse. A
recurring statistic (see below) is always quoted, so every year's version is kept.

Refusal gate (strict on purpose: a confident wrong answer costs more than "please contact us"): the passage must
cover most of the question's information (idf-weighted concepts), so a question about something the site never
mentions is refused instead of answered with a loosely related sentence. Questions asking for a rate never come
here: the rate tables are the only place rates are read from, so a rate they don't hold is refused.

Recurring statistics: when the chosen sentence has a twin on another dated article with different numbers
("294 employees used VTO in 2023" / "256 ... in 2024"), every version is given, newest article first.
"""
from __future__ import annotations

import re
from typing import Optional

from . import llm
from .question import Question
from .reply import clean_text, numbers_ok, refusal
from .search import AUTHORITY, Hit, Index, concepts, terms
from .shapes import Answer, Source
from .store import SnapshotData

GATE = 0.6            # share of the question's information the chosen passage must cover (no key)
LLM_GATE = 0.45       # looser pre-filter when the LLM can still answer NOT_FOUND
TWIN = 0.6            # word overlap (Jaccard) for two sentences to be versions of one statistic
MIN_WORDS = 6         # shorter "sentences" are labels, buttons or form options
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"(])")
_ABBREV = re.compile(r"\b(?:[A-Z]|[A-Z][a-z]{1,3}|[a-z]\.[a-z]|No|St|Ave|Rd|Blvd|Inc|Co|Jan|Feb|Mar|Apr|Aug|Sept?|Oct|"
                     r"Nov|Dec|Mr|Mrs|Ms|Dr|U\.S)\.$")
_CONTINUES = re.compile(r"^(?:This|These|That|It|Its|They|Their|The (?:program|initiative|grant|benefit|account|card|"
                        r"loan|service|offer|app|feature)|You can|Members|Plus|Also|In addition)\b")
_NUMBERS = re.compile(r"\d[\d,.]*\d|\d")
_YEAR = re.compile(r"\b(?:19|20)\d\d\b")


def sentences(text: str) -> list[str]:
    """Sentences of a passage: each line is at least one sentence; lines split at sentence ends (not after
    abbreviations such as "Sept." or "St.")."""
    out = []
    for line in text.split("\n"):
        parts = _SPLIT.split(line.strip())
        merged: list[str] = []
        for p in parts:
            if merged and _ABBREV.search(merged[-1]):
                merged[-1] += " " + p
            else:
                merged.append(p)
        for s in merged:
            s = s.strip()
            if s:
                out.append(s if s[-1] in ".!?:)”\"" else s + ".")
    return out


def _quotable(s: str) -> bool:
    """A real sentence: long enough, not a question, not someone's quote, not a lead-in to a list."""
    return (len(s.split()) >= MIN_WORDS and not s.endswith(("?", ":")) and not s.startswith(("“", '"')))


# "What is the late fee?" / "How much is ...?" want a value: a sentence without one doesn't answer it.
VALUE_Q = re.compile(r"\b(?:how much|how many|what(?:'s| is| are) the (?:\w+ )?(?:fee|fees|cost|price|charge|limit|"
                     r"minimum|maximum|amount|penalty))\b")
_HAS_VALUE = re.compile(r"\d|\b(?:free|no (?:fee|charge|cost)|not charge|doesn'?t charge|at no (?:extra )?cost)\b", re.I)


def _pick_sentences(q: Question, cs: list[frozenset], hits: list[Hit],
                    index: Index) -> Optional[tuple[Hit, list[str]]]:
    """The best sentence among the passages that pass the gate: most question information inside the sentence
    itself (headings count too, for FAQ answers), lightly weighted by page authority, then the better passage,
    then the earlier position on the page (a press release's lead says the most).
    A sentence must mention the question's most distinctive concept (in itself, its heading or its page title),
    must hold a value when the question asks for one, and business pages count less unless the question says
    business (the same "personal unless business" rule the rate answers use)."""
    best = None
    rarest = max(cs, key=index.concept_idf)
    wants_value = bool(VALUE_Q.search(q.text))
    business_q = "business" in q.text
    page_rank: dict[str, int] = {}
    for h in hits:
        page_rank.setdefault(h.passage.url, len(page_rank))
    for h in hits:
        if index.coverage(cs, h.matched) < GATE:
            continue
        sents = sentences(h.passage.text)
        context = set(terms(h.passage.heading))
        title = set(terms(h.passage.title))
        weight = _authority(h) ** 0.3 * (0.8 if h.passage.path.startswith("/business") and not business_q else 1.0)
        for j, s in enumerate(sents):
            if not _quotable(s) or (wants_value and not _HAS_VALUE.search(s)):
                continue
            inside = set(terms(s))
            if not rarest & (inside | context | title):
                continue
            score = sum(index.concept_idf(c) * (1.0 if c & inside else 0.6 if c & context else 0.0) for c in cs)
            key = (-round(score * weight, 3), page_rank[h.passage.url], h.passage.pos, j)
            if best is None or key < best[0]:
                best = (key, h, sents, j)
    if best is None:
        return None
    _, h, sents, j = best
    chosen = [sents[j]]
    if j + 1 < len(sents) and _CONTINUES.match(sents[j + 1]) and _quotable(sents[j + 1]):
        chosen.append(sents[j + 1])
    return h, chosen


def _authority(h: Hit) -> float:
    return AUTHORITY.get(h.passage.kind, 1.0)


def _twins(sentence: str, hit: Hit, index: Index) -> list[tuple[str, Hit]]:
    """Other dated articles stating the same sentence with different numbers (a statistic reported every year)."""
    if not _NUMBERS.search(sentence) or not hit.passage.published:
        return []
    words = {t for t in terms(sentence) if not t.isdigit()}
    nums = set(_NUMBERS.findall(sentence))
    years = set(_YEAR.findall(sentence))
    if not years:
        return []                       # only a figure reported "for a year" has versions to list side by side
    out, seen = [], {hit.passage.url}
    for other in index.search([frozenset((w,)) for w in sorted(words)], k=10):
        if other.passage.url in seen or not other.passage.published:
            continue
        for s in sentences(other.passage.text):
            sw = {t for t in terms(s) if not t.isdigit()}
            other_years = set(_YEAR.findall(s))
            if (words and len(words & sw) / len(words | sw) >= TWIN and set(_NUMBERS.findall(s)) != nums
                    and other_years and other_years != years):
                out.append((s, other))
                seen.add(other.passage.url)
                break
    return out


def _sources(snap: SnapshotData, picked: list[tuple[str, Hit]], extra: list[Hit]) -> list[Source]:
    out, seen = [], set()
    for quote, h in picked:
        for url in (h.passage.url, *h.also):
            if url not in seen and len(out) < 5:
                seen.add(url)
                out.append(Source(url=url, snapshot_id=snap.snapshot_id, quote=quote[:300]))
    for h in extra:
        if h.passage.url not in seen and len(out) < 5:
            seen.add(h.passage.url)
            out.append(Source(url=h.passage.url, snapshot_id=snap.snapshot_id, quote=h.passage.text[:300]))
    return out


def _quoted(q: Question, snap: SnapshotData, cs: list[frozenset],
            hits: list[Hit]) -> tuple[Optional[Answer], bool]:
    """The quoted answer, and whether it lists several dated versions of one statistic."""
    found = _pick_sentences(q, cs, hits, snap.index)
    if not found:
        return None, False
    best, chosen = found
    picked = [(" ".join(chosen), best)]
    twins = _twins(chosen[0], best, snap.index)
    if twins:
        picked = sorted([(chosen[0], best), *twins], key=lambda x: x[1].passage.published or "", reverse=True)
    body = clean_text(" ".join(s for s, _ in picked))
    if not body:
        return None, False              # every sentence held a banned phrase: refuse rather than answer nothing
    title = best.passage.title.split(" | ")[0].strip()
    text = f"{body} (Source: Dupaco's “{title}” page.)"
    extra = [h for h in hits[:5] if h is not best]
    return Answer(text=text, sources=_sources(snap, picked, extra), confident=True), bool(twins)


def search_answer(q: Question, snap: SnapshotData) -> Optional[Answer]:
    cs = concepts(q.text)
    if not cs:
        return None
    hits = snap.index.search(cs, k=10)
    if not hits:
        return None
    quoted, versions = _quoted(q, snap, cs, hits)
    # A statistic reported every year: the quoted answer already lists every version, newest first. An LLM summary
    # tends to keep only the newest, which drops the year a question like "last year" may mean.
    if llm.available() and not versions:
        candidates = [h for h in hits[:6] if snap.index.coverage(cs, h.matched) >= LLM_GATE]
        if candidates:
            reply = llm.answer(q.raw, [(h.passage.title, h.passage.url, h.passage.published, h.passage.text)
                                       for h in candidates])
            if reply == llm.NOT_FOUND:
                return refusal()
            evidence = " ".join(h.passage.text for h in candidates)
            if reply and numbers_ok(reply, evidence + " " + q.raw) and clean_text(reply):
                return Answer(text=clean_text(reply), sources=_sources(snap, [], candidates), confident=True)
    return quoted
