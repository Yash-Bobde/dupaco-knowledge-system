"""Route a question to the answer type that can handle it; refuse when none can.

Order: rate questions (truth table) -> branch questions (addresses, hours, closures) -> refusal. Each answer type
returns None when the question isn't one it can read, so the next one gets a turn.
"""
from __future__ import annotations

from typing import Optional

from .answer_branches import branch_answer
from .answer_rates import rate_answer
from .question import read
from .reply import refusal
from .shapes import Answer
from .store import Store


def answer_question(store: Store, question: str, as_of: Optional[str] = None,
                    cache: Optional[dict] = None) -> Answer:
    """Answers are built deterministically from the question, so paraphrases naming the same things get the same
    text. The cache (snapshot + normalized question) only makes repeats instant. It is deliberately not keyed on
    the parsed slots: two questions with equal slots can still differ (a link question, an ARM question)."""
    q = read(question)
    snap = store.at(as_of or q.as_of)
    ref_date = (as_of or q.as_of or snap.taken_on)[:10]   # the date the question is about (R6: closed yet?)
    key = (snap.snapshot_id, ref_date, q.text)
    cache = {} if cache is None else cache
    if key not in cache:
        cache[key] = rate_answer(q, snap) or branch_answer(q, snap, ref_date) or refusal()
    return cache[key]
