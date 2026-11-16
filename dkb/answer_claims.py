"""Answers about the company: dated claims (branch count, members, assets, employees, service area, Forbes), the
routing number and main phone, who can join, how to join, and how to update contact details.

Dated claims follow R3 (the newest article as of the question's date wins). Older values never appear in the text
(old values and years are trap tokens); they go in conflict_note.
"""
from __future__ import annotations

import re
from typing import Optional

from . import links as L
from .claims import current
from .question import Question
from .reply import refusal
from .shapes import Answer, Source
from .store import SnapshotData

ROUTING_Q = re.compile(r"\b(?:routing|aba|transit)\b")
FORBES_Q = re.compile(r"\bforbes\b")


def _count_q(nouns: str) -> re.Pattern:
    """"how many [word] <noun>" asked of Dupaco as a whole: nothing after the noun but "does Dupaco have",
    "work at Dupaco", "are there" and the like. "How many Dupaco employees used Volunteer Time Off last year?"
    is a different fact (a count of an activity), so it must not get the headcount."""
    return re.compile(rf"\bhow many (?:[\w-]+ )?(?:{nouns})(?:\s+(?:does|do|are|is|work|works)\b[^?]*)?\s*\??$|"
                      rf"\bnumber of (?:{nouns})\b")


BRANCH_COUNT_Q = re.compile(_count_q(r"branch(?:es)?|branch locations|locations|offices").pattern +
                            r"|\b\d+ (?:full[- ]service )?(?:branch(?:es)?|locations|offices)\b")
MEMBERS_Q = re.compile(_count_q("members").pattern + r"|\bmembership size\b")
EMPLOYEES_Q = re.compile(_count_q("employees|people|staff|staff members").pattern)
ASSETS_Q = re.compile(r"\b(?:dupaco'?s (?:total )?assets|total assets|assets (?:does|do) dupaco|how (?:big|large) is dupaco|"
                      r"in assets|assets (?:exceed\w*|of|over|above|worth)|billion in)\b")
# Membership eligibility only: "Do I qualify for an auto loan?" is about a loan, not about joining.
ELIGIBLE_Q = re.compile(r"\b(?:eligib\w*|qualif\w*|who can (?:join|become)|can (?:i|anyone|anybody) (?:join|become)|"
                        r"requirements? (?:to|for) (?:join|membership|become))")
MEMBERSHIP_WORDS = re.compile(r"\b(?:members?|membership|join)\b")
AREA_Q = re.compile(r"\b(?:which|what) (?:states?|areas?|counties|regions?)\b|\bservice area\b|"
                    r"\bwhere (?:does|is) dupaco (?:serve|operate|located|based)\b|\bwhere is dupaco\b")
JOIN_Q = re.compile(r"\b(?:join(?:ing)? (?:dupaco|the credit union)|become a (?:dupaco )?member|"
                    r"sign up for (?:a )?(?:dupaco )?membership|open a membership)\b|\bjoin(?: online)?\s*\??$")
UPDATE_Q = re.compile(r"\b(?:update|change|correct)\b.*\b(?:contact|address|phone|e-?mail|information|info|name)\b")
# Asking for Dupaco's number, not just mentioning a phone ("Can I deposit a check with my phone?" is not one).
PHONE_Q = re.compile(r"\b(?:(?:phone|telephone|contact|toll[- ]free) (?:number|#)|number (?:to|i can) call|"
                     r"(?:call|contact|reach|phone|talk to|speak (?:to|with)) (?:dupaco|you|someone|a person|"
                     r"the credit union|customer service|member service|member care)|customer service|member service)\b")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")     # "$3.6 billion" -> "3.6", not "3" and "6"


def _source(url: str, snap: SnapshotData, quote: Optional[str]) -> Source:
    return Source(url=url, snapshot_id=snap.snapshot_id, quote=quote)


def _claim_answer(snap: SnapshotData, topic: str, ref_date: str, template: str, q: Question) -> Answer:
    winner, agreeing, older = current(snap.claims, topic, ref_date)
    if not winner:
        return refusal()
    text = template.format(value=winner.value)
    asked_text = q.text.replace(q.date_phrase.lower(), " ") if q.date_phrase else q.text   # not the date's year
    if q.yes_no and (asked := _NUMBER.findall(asked_text)):
        stated = {n.replace(",", "") for n in _NUMBER.findall(winner.value)}
        same = all(a.replace(",", "") in stated for a in asked)
        text = ("Yes. " if same else "No. ") + text
    sources = [_source(c.url, snap, c.sentence) for c in [winner, *agreeing][:5]]
    note = None
    if older:
        olds = "; ".join(f"{c.value} (article of {c.published})" for c in older[:4])
        note = f"Older releases state other values: {olds}. The newest article ({winner.published}) wins."
    return Answer(text=text, sources=sources, confident=True, conflict_note=note)


def _forbes(snap: SnapshotData, ref_date: str) -> Answer:
    count, _, older = current(snap.claims, "forbes_count", ref_date)
    recog, _, _ = current(snap.claims, "recognition", ref_date)
    parts, sources = [], []
    if recog:
        honors = [h.strip() for h in re.split(r",\s*(?:and\s+)?|\s+and\s+", recog.value) if "Forbes" in h]
        honors = [re.sub(r"^an? ", "", h) for h in honors]
        if honors:
            parts.append("Dupaco is a " + " and a ".join(honors) + ".")
            sources.append(_source(recog.url, snap, recog.sentence))
    if count:
        parts.append(f"Forbes has named Dupaco one of the top-rated credit unions in America; its latest Best-in-State "
                     f"ranking was the {count.value} time Forbes recognized Dupaco.")
        sources.insert(0, _source(count.url, snap, count.sentence))
    if not parts:
        return refusal()
    note = None
    if count and older:
        note = ("Older Forbes releases give older counts: " + "; ".join(f"{c.value} ({c.published})" for c in older)
                + f". The newest release ({count.published}) wins.")
    return Answer(text=" ".join(parts), sources=sources, confident=True, conflict_note=note)


_SOURCE_ORDER = {"contact": 0, "product": 1, "rate_sheet": 2, "other": 3, "home": 3, "landing": 4, "blog": 5,
                 "press": 5, "calculator": 6, "file": 7}


def _site_value(snap: SnapshotData, topic: str, template: str) -> Answer:
    sv = snap.site.get(topic)
    if not sv:
        return refusal()
    pages = sorted(sv.pages, key=lambda t: (_SOURCE_ORDER.get(t[0], 5), t[1]))[:5]
    return Answer(text=template.format(value=sv.value), sources=[_source(u, snap, s) for _, u, s in pages],
                  confident=True)


def _join_url(snap: SnapshotData) -> Optional[tuple[str, list]]:
    return L.best_target(snap.links, snap.live_paths, L.JOIN_ANCHOR, prefer_offsite=True)


def _eligibility(snap: SnapshotData, ref_date: str) -> Answer:
    m = snap.membership
    if not m:
        return refusal()
    conditions = [re.sub(r"\bour\b", "Dupaco's", c[0].lower() + c[1:]) for c in m.conditions]
    text = "You can join Dupaco if " + ("any of these apply: " + "; ".join(conditions) if len(conditions) > 1
                                       else conditions[0]) + "."
    area, _, _ = current(snap.claims, "service_area", ref_date)
    sources = [_source(m.url, snap, " ".join(m.conditions))]
    if area:
        text += f" Dupaco serves residents in {area.value}."
        sources.append(_source(area.url, snap, area.sentence))
    elif m.states:
        text += f" Dupaco is located in {m.states}."
    if m.how:
        text += f" {m.how}"
    if found := _join_url(snap):
        text += f" You can apply online at {found[0]}."
    return Answer(text=text, sources=sources, confident=True)


def _join(snap: SnapshotData) -> Answer:
    found = _join_url(snap)
    m = snap.membership
    if not found and not (m and m.how):
        return refusal()
    parts, sources = [], []
    if found:
        parts.append(f"You can join Dupaco online at {found[0]}.")
        sources.append(_source(found[0], snap, None))
    if m and m.how:
        parts.append(m.how)
        sources.append(_source(m.url, snap, m.how))
    return Answer(text=" ".join(parts), sources=sources, confident=True)


def _update_contact(snap: SnapshotData) -> Answer:
    phone = snap.site.get("phone")
    shine = L.best_target(snap.links, snap.live_paths, L.SHINE_ANCHOR, prefer_offsite=True)
    if not phone and not shine:
        return refusal()
    parts = []
    if shine:
        parts.append(f"You can review and change your personal information by logging in to Shine Online Banking "
                     f"at {shine[0]} and editing your profile")
    if phone:
        parts.append(f"call Dupaco at {phone.value}")
    text = (", or ".join(parts) + ".")
    text = text[0].upper() + text[1:]
    sources = []
    if phone:
        sources += _site_value(snap, "phone", "{value}").sources[:3]
    if shine:
        sources.insert(0, _source(shine[0], snap, None))
    return Answer(text=text, sources=sources, confident=True)


def claim_answer(q: Question, snap: SnapshotData, ref_date: str) -> Optional[Answer]:
    text = q.text
    if ROUTING_Q.search(text):
        return _site_value(snap, "routing_number", "Dupaco's routing number is {value}.")
    if FORBES_Q.search(text):
        return _forbes(snap, ref_date)
    if BRANCH_COUNT_Q.search(text):
        return _claim_answer(snap, "branch_count", ref_date, "Dupaco has {value} branch office locations.", q)
    if MEMBERS_Q.search(text):
        return _claim_answer(snap, "members", ref_date, "Dupaco's membership has grown to {value}.", q)
    if EMPLOYEES_Q.search(text):
        return _claim_answer(snap, "employees", ref_date, "Dupaco has {value} employees.", q)
    if ASSETS_Q.search(text):
        return _claim_answer(snap, "assets", ref_date, "Dupaco has assets exceeding {value}.", q)
    if ELIGIBLE_Q.search(text) and MEMBERSHIP_WORDS.search(text) and not q.families:
        return _eligibility(snap, ref_date)
    if AREA_Q.search(text):
        return _claim_answer(snap, "service_area", ref_date, "Dupaco serves residents in {value}.", q)
    if JOIN_Q.search(text):
        return _join(snap)
    if UPDATE_Q.search(text):
        return _update_contact(snap)
    if PHONE_Q.search(text):
        return _site_value(snap, "phone", "You can call Dupaco at {value}.")
    return None
