"""Versioned store: every snapshot's cleaned pages, tables and rate facts, and the snapshot to use for a date.

R0: an `as_of` date uses the newest snapshot taken on or before that date; no date uses the latest snapshot.
A date before the first snapshot falls back to the oldest one (the earliest view of the site we have).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import branches as branch_records
from .branches import Branch, Closure
from .facts import RateFact, rate_facts
from .pages import CleanPage, clean_page
from .tables import Table, find_tables


@dataclass
class SnapshotData:
    snapshot_id: str
    taken_on: str                                               # ISO date
    pages: dict[str, CleanPage] = field(default_factory=dict)   # url -> page (urls are unique; paths are not)
    tables: dict[str, list[Table]] = field(default_factory=dict)  # url -> tables on that page
    facts: list[RateFact] = field(default_factory=list)
    branches: list[Branch] = field(default_factory=list)        # from /contact and branch-opening releases
    closures: list[Closure] = field(default_factory=list)       # branch-closure announcements


class Store:
    def __init__(self) -> None:
        self.snapshots: list[SnapshotData] = []   # oldest first

    def add(self, snapshot) -> SnapshotData:
        """Clean every page of a harness Snapshot, parse its tables and extract its rate facts."""
        data = SnapshotData(snapshot.snapshot_id, snapshot.taken_on)
        for p in snapshot.pages:
            page = clean_page(p.url, p.title, p.markdown)
            tables = find_tables(page.text)
            data.pages[p.url] = page
            data.tables[p.url] = tables
            data.facts.extend(rate_facts(snapshot.snapshot_id, page, tables))
        data.branches, data.closures = branch_records.extract(list(data.pages.values()))
        self.snapshots.append(data)
        self.snapshots.sort(key=lambda s: s.taken_on)
        return data

    @property
    def latest(self) -> SnapshotData:
        return self.snapshots[-1]

    def at(self, as_of: Optional[str]) -> SnapshotData:
        """The snapshot that shows the site as of `as_of` (ISO date or datetime), or the latest one."""
        if not as_of:
            return self.latest
        day = as_of[:10]
        eligible = [s for s in self.snapshots if s.taken_on <= day]
        return eligible[-1] if eligible else self.snapshots[0]
