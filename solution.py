"""Dupaco knowledge system: the entry point the harness loads.

build() returns an object with ingest / answer / conflicts (shapes in harness/contract.py).
The real work lives in the `dkb` package; this file only wires the parts together.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# The harness already puts this folder on sys.path; this keeps `import dkb` working when
# solution.py is loaded some other way (e.g. from our own scripts).
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from dkb.answer import answer_question  # noqa: E402
from dkb.shapes import Answer, Conflict, Snapshot  # noqa: E402
from dkb.store import Store  # noqa: E402


class DupacoKnowledge:
    def __init__(self) -> None:
        self.store = Store()        # every snapshot's cleaned pages, tables and rate facts
        self._answers: dict = {}    # (snapshot, question slots) -> Answer

    def ingest(self, snapshot: Snapshot) -> None:
        self.store.add(snapshot)
        self._answers.clear()

    def answer(self, question: str, as_of: Optional[str] = None) -> Answer:
        return answer_question(self.store, question, as_of, self._answers)

    def conflicts(self) -> list[Conflict]:
        return []


def build() -> DupacoKnowledge:
    return DupacoKnowledge()
