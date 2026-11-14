"""Branch answers: addresses, lobby / drive-up hours, and closures.

- A named branch -> its address / hours / phone. A branch name the site doesn't know ("the Mullan Avenue branch")
  -> refusal, never another branch's data.
- General hours -> every distinct schedule on file with the branches that keep it ("Hours vary by branch"),
  most common first; times copied as printed.
- A closed (or closing) branch -> "closed on <date>" or "will close on <date>", judged against the date the question
  is about (R6), plus the site's own sentence on where members can go instead.
"""
from __future__ import annotations

import re
from typing import Optional

from .branches import Branch, Closure
from .question import Question
from .reply import LINK_WORDS, refusal
from .shapes import Answer, Source
from .store import SnapshotData

LOCATOR_URL = "https://www.dupaco.com/contact"
BRANCH_WORDS = re.compile(r"\b(?:branch(?:es)?|locations?|lobby|drive[- ]?(?:up|thru|through)|hours?|address|located)\b")
HOURS_WORDS = re.compile(r"\b(?:hours?|open|what time|lobby|drive[- ]?(?:up|thru|through))\b")
ADDRESS_WORDS = re.compile(r"\b(?:address|located|location of|where is|where are|directions)\b")
PHONE_WORDS = re.compile(r"\b(?:phone|telephone|call)\b")
CLOSE_WORDS = re.compile(r"\b(?:clos(?:e|es|ed|ing|ure)|still open|shut|last day)\b")
_NAMED = re.compile(r"\bthe ((?:[\w'-]+ ){0,3}?[\w'-]+) (?:branch|location|office)\b")
_NOT_A_NAME = {"dupaco", "nearest", "closest", "local", "main", "new", "same", "other", "your", "my", "each", "every",
               "all", "full-service", "learning", "lab", "this", "that", "which", "credit", "union", "a", "one", "open"}


def _named(keys, text: str) -> bool:
    return any(re.search(rf"\b{re.escape(k)}\b", text) for k in keys)


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _sources(urls: list[str], snap: SnapshotData, limit: int = 5) -> list[Source]:
    out = []
    for url in dict.fromkeys(urls):
        page = snap.pages.get(url)
        out.append(Source(url=url, snapshot_id=snap.snapshot_id, quote=(page.title if page else None)))
        if len(out) == limit:
            break
    return out


def branch_answer(q: Question, snap: SnapshotData, ref_date: str) -> Optional[Answer]:
    text = q.text
    if not BRANCH_WORDS.search(text) and not CLOSE_WORDS.search(text):
        return None
    if LINK_WORDS.search(text) or re.search(r"\bhow many\b", text):
        return None    # locator links and branch counts are answered elsewhere
    closures = [c for c in snap.closures if (c.published or "") <= ref_date and _named(c.keys, text)]
    if closures:
        return _closure(closures[0], q, snap, ref_date)
    named = [b for b in snap.branches if _named(b.keys, text)]
    if named:
        return _specific(named, q, snap)
    m = _NAMED.search(text)
    if m and [w for w in m.group(1).split() if w not in _NOT_A_NAME]:
        return refusal()    # a branch the site has no record of
    if HOURS_WORDS.search(text):
        return _general_hours(q, snap)
    if ADDRESS_WORDS.search(text) and BRANCH_WORDS.search(text):
        return _all_addresses(snap)
    return None


def _closure(c: Closure, q: Question, snap: SnapshotData, ref_date: str) -> Answer:
    past = c.date <= ref_date
    if c.last_day:
        sentence = f"The {c.branch} branch's last day of service {'was' if past else 'will be'} {c.date_text}."
    else:
        sentence = f"The {c.branch} branch {'closed on' if past else 'will close on'} {c.date_text}."
    if q.yes_no and re.search(r"\b(?:open|still)\b", q.text):
        sentence = ("No. " if past else "Yes, for now. ") + sentence
    text = sentence + (f" {c.instead}" if c.instead else "")
    return Answer(text=text, sources=_sources([c.url], snap), confident=True)


def _hours_lines(b: Branch, kinds: list[str]) -> str:
    parts = []
    if "lobby" in kinds and b.lobby:
        parts.append(f"Lobby: {'; '.join(b.lobby)}")
    if "drive_up" in kinds and b.drive_up:
        parts.append(f"Drive-up: {'; '.join(b.drive_up)}")
    return ". ".join(parts)


def _kinds(text: str) -> list[str]:
    lobby, drive = "lobby" in text, bool(re.search(r"drive[- ]?(?:up|thru|through)", text))
    return ["lobby"] if lobby and not drive else ["drive_up"] if drive and not lobby else ["lobby", "drive_up"]


def _address(b: Branch) -> Optional[str]:
    if not b.address:
        return None
    return b.address if "," in b.address else f"{b.address}, {b.city}"


def _specific(named: list[Branch], q: Question, snap: SnapshotData) -> Answer:
    text = q.text
    want_hours, want_phone = bool(HOURS_WORDS.search(text)), bool(PHONE_WORDS.search(text))
    want_address = bool(ADDRESS_WORDS.search(text)) or not (want_hours or want_phone)
    sentences, urls = [], []
    for b in named:
        bits = []
        if want_address and (addr := _address(b)):
            bits.append(f"The {b.name} branch is at {addr}.")
        if want_hours and (b.lobby or b.drive_up):
            bits.append(f"{b.name} branch hours. {_hours_lines(b, _kinds(text))}.")
        if want_phone and b.phone:
            bits.append(f"The {b.name} branch phone number is {b.phone}.")
        if bits:
            sentences += bits
            urls.append(b.url)
    if not sentences:
        return refusal()    # e.g. hours asked for a branch whose hours the site doesn't list
    return Answer(text=" ".join(sentences), sources=_sources(urls, snap), confident=True)


def _general_hours(q: Question, snap: SnapshotData) -> Answer:
    records = [b for b in snap.branches if b.lobby or b.drive_up]
    if not records:
        return refusal()
    out, urls = [], []
    for kind in _kinds(q.text):
        groups: dict[str, list[Branch]] = {}
        for b in records:
            lines = getattr(b, kind)
            if lines:
                key = re.sub(r"\s+", "", "|".join(lines).lower().replace("–", "-").replace("—", "-"))
                groups.setdefault(key, []).append(b)
        ordered = sorted(groups.values(), key=lambda bs: (-len(bs), -max(int((b.published or "0").replace("-", "")) for b in bs)))
        label = "Lobby" if kind == "lobby" else "Drive-up"
        parts = []
        for bs in ordered:
            newest = max(bs, key=lambda b: b.published or "")
            parts.append(f"{_and([b.name for b in bs])}: {'; '.join(getattr(newest, kind))}.")
            urls.append(newest.url)
        out.append(f"{label} hours vary by branch. " + " ".join(parts))
    text = " ".join(out) + f" Find a specific location's details at {LOCATOR_URL}."
    if q.yes_no and re.search(r"\b(?:all|every|same)\b", q.text):
        text = "No. " + text
    return Answer(text=text, sources=_sources(urls + [LOCATOR_URL], snap), confident=True)


def _all_addresses(snap: SnapshotData) -> Answer:
    with_address = [b for b in snap.branches if b.address]
    if not with_address:
        return refusal()
    text = " ".join(f"{b.name}: {_address(b)}." for b in with_address)
    text += f" The full branch and ATM locator is at {LOCATOR_URL}."
    return Answer(text=text, sources=_sources([LOCATOR_URL] + [b.url for b in with_address], snap), confident=True)
