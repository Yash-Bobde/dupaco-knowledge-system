"""Shared reply pieces: the refusal, the last checks every free-text answer passes, and shared patterns."""
from __future__ import annotations

import re

from .shapes import Answer

# A refusal must carry no numbers at all (the scorer fails a refusal that states any number not in the question),
# so it names no phone number; "isn't listed" + "Please contact" + confident=False all mark it as a refusal.
REFUSAL = "That isn't listed on Dupaco's website. Please contact Dupaco directly for help."
LINK_WORDS = re.compile(r"\b(?:link|url|web ?page|website)\b")

# Chatbot habits members complained about: asking for identifiers, talking about "the facts", asking back.
BANNED = re.compile(r"zip code|member number|in these facts|unable to find|don'?t have that detail|"
                    r"which certificate term would you like|\bthe (?:context|passages?)\b", re.I)
_NUM = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def refusal() -> Answer:
    return Answer(text=REFUSAL, sources=[], confident=False)


def numbers(text: str) -> set[float]:
    return {round(float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else "")), 4)
            for m in _NUM.finditer(text or "")}


def numbers_ok(text: str, evidence: str) -> bool:
    """Every number in the text appears in the evidence (or the question, passed in with it)."""
    return numbers(text) <= numbers(evidence)


def clean_text(text: str) -> str:
    """Remove markdown emphasis and placeholders, and any sentence with a banned phrase."""
    text = re.sub(r"\*{2,}|<[^>]{0,40}>", "", text)
    kept = [s for s in _SENTENCE.split(text) if not BANNED.search(s)]
    return re.sub(r"\s+", " ", " ".join(kept)).strip()
