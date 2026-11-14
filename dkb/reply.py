"""Shared reply pieces: the refusal, and patterns several answer types use."""
from __future__ import annotations

import re

from .shapes import Answer

# A refusal must carry no numbers at all (the scorer fails a refusal that states any number not in the question),
# so it names no phone number; "isn't listed" + "Please contact" + confident=False all mark it as a refusal.
REFUSAL = "That isn't listed on Dupaco's website. Please contact Dupaco directly for help."
LINK_WORDS = re.compile(r"\b(?:link|url|web ?page|website)\b")


def refusal() -> Answer:
    return Answer(text=REFUSAL, sources=[], confident=False)
