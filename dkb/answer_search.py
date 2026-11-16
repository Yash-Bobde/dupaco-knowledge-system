"""Open-ended answers from the site's own sentences, for questions no template covers.

No key: quote the best sentence (plus the one after it when it continues the thought), verbatim, and cite its page.
With OPENAI_API_KEY: the LLM writes 1-3 sentences from the top passages only (llm.py); if it fails or states a
number the passages don't contain, the quoted answer (or a refusal) is used instead; NOT_FOUND means refuse.

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
    return len(s.split()) >= MIN_WORDS and not s.endswith("?") and not s.startswith(("“", '"'))


def _pick_sentences(cs: list[frozenset], hits: list[Hit], index: Index) -> Optional[tuple[Hit, list[str]]]:
    """The best sentence among the passages that pass the gate: most question information inside the sentence
    itself (headings count too, for FAQ answers), lightly weighted by page authority, then the better passage,
    then the earlier position on the page (a press release's lead says the most)."""
    best = None
    page_rank: dict[str, int] = {}
    for h in hits:
        page_rank.setdefault(h.passage.url, len(page_rank))
    for rank, h in enumerate(hits):
        if index.coverage(cs, h.matched) < GATE:
            continue
        sents = sentences(h.passage.text)
        context = set(terms(h.passage.heading))
        for j, s in enumerate(sents):
            if not _quotable(s):
                continue
            inside = set(terms(s))
            score = sum(index.concept_idf(c) * (1.0 if c & inside else 0.6 if c & context else 0.0) for c in cs)
            key = (-round(score * _authority(h) ** 0.3, 3), page_rank[h.passage.url], h.passage.pos, j)
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
    out, seen = [], {hit.passage.url}
    for other in index.search([frozenset((w,)) for w in sorted(words)], k=10):
        if other.passage.url in seen or not other.passage.published:
            continue
        for s in sentences(other.passage.text):
            sw = {t for t in terms(s) if not t.isdigit()}
            if words and len(words & sw) / len(words | sw) >= TWIN and set(_NUMBERS.findall(s)) != nums:
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


def _quoted(snap: SnapshotData, cs: list[frozenset], hits: list[Hit]) -> Optional[Answer]:
    found = _pick_sentences(cs, hits, snap.index)
    if not found:
        return None
    best, chosen = found
    picked = [(" ".join(chosen), best)]
    twins = _twins(chosen[0], best, snap.index)
    if twins:
        picked = sorted([(chosen[0], best), *twins], key=lambda x: x[1].passage.published or "", reverse=True)
    body = " ".join(s for s, _ in picked)
    text = clean_text(f"{body} (Source: Dupaco's “{best.passage.title}” page.)")
    if not text:
        return None
    extra = [h for h in hits[:5] if h is not best]
    return Answer(text=text, sources=_sources(snap, picked, extra), confident=True)


def search_answer(q: Question, snap: SnapshotData) -> Optional[Answer]:
    cs = concepts(q.text)
    if not cs:
        return None
    hits = snap.index.search(cs, k=10)
    if not hits:
        return None
    if llm.available():
        candidates = [h for h in hits[:6] if snap.index.coverage(cs, h.matched) >= LLM_GATE]
        if candidates:
            reply = llm.answer(q.raw, [(h.passage.title, h.passage.url, h.passage.published, h.passage.text)
                                       for h in candidates])
            if reply == llm.NOT_FOUND:
                return refusal()
            evidence = " ".join(h.passage.text for h in candidates)
            if reply and numbers_ok(reply, evidence + " " + q.raw) and clean_text(reply):
                return Answer(text=clean_text(reply), sources=_sources(snap, [], candidates), confident=True)
    return _quoted(snap, cs, hits)
