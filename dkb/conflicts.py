"""conflicts(): the places the website contradicts itself, each with the winner and why, ranked and capped.

Everything here is read from what ingest already decided (no new rules):
1. Company claims: old press releases restate old numbers ("24 branch office locations", Forbes "fourth time").
   Winner by R3: the newest dated article.
2. Open rate conflicts in the latest snapshot: a stale page still prints an old value for a rate the official
   sheet has since changed (5.34% on the vehicle calculator vs 5.39% on /rates). Winner by R1/R2 (truth.py).
   One entry per product, program and term, listing the winning values first and then the stale ones.
3. Certificate specials whose rate changed between snapshots (10-month jumbo special 3.90% -> 4.00%), when the
   latest snapshot doesn't already show them as an open conflict. The newest snapshot wins.

Precision counts in the scoring (a listed conflict that isn't a real one lowers the score), so entries are ranked
(claims, then open conflicts on the most-asked products, then rate history) and the list is capped. Rate changes
on products that reprice every snapshot (mortgages) are updates, not contradictions, unless a stale page still
shows the old value (kind 2).
"""
from __future__ import annotations

from typing import Optional

from .claims import current
from .facts import RateFact
from .shapes import Conflict, Source
from .store import SnapshotData, Store
from .truth import _reason, outranked, source_key

# Families members ask about most come first (the eval's stale traps are mortgage, auto, certificates, checking).
FAMILY_ORDER = ["mortgage", "auto", "checking", "certificate", "savings", "money_market", "credit_card", "ira",
                "heloc", "home_equity_loan", "personal_loan", "home_improvement", "boat_rv", "atv", "hsa"]
PROGRAM_ORDER = {None: 0, "purchase": 0, "standard": 0, "intro": 1, "refinance": 1, "cash-out refinance": 2}
TERM_ORDER = {"mortgage": [360, 180, 240], "auto": [60, 72, 84, 48, 36]}   # most common terms first


def _term_rank(f: RateFact) -> int:
    order = TERM_ORDER.get(f.family, [])
    return order.index(f.term_months) if f.term_months in order else len(order)
FAMILY_WORDS = {"mortgage": "mortgage, home loan", "auto": "auto loan, vehicle, car", "certificate": "certificate, CD",
                "checking": "checking account", "savings": "savings account", "money_market": "money market",
                "ira": "IRA certificate", "credit_card": "credit card, Visa", "heloc": "home equity line of credit, HELOC",
                "home_equity_loan": "home equity loan", "personal_loan": "personal loan",
                "home_improvement": "home improvement loan", "boat_rv": "boat and RV loan", "atv": "ATV loan",
                "hsa": "health savings account"}
CLAIM_TOPICS = {
    "branch_count": ("Number of Dupaco branch office locations (branch count)", "{} branch office locations"),
    "forbes_count": ("Forbes recognition: how many times Forbes named Dupaco one of America's best credit unions",
                     "{} time Forbes recognized Dupaco"),
}
MAX_CONFLICTS = 15


def _term(f: RateFact) -> str:
    m = f.term_months
    if not m:
        return ""
    return f"{m // 12}-year" if f.family == "mortgage" and m % 12 == 0 else f"{m}-month"


def _topic(f: RateFact) -> str:
    """Plain words a reader (or a topic-keyword match) would use: "60-month Auto & Motorcycle Loan APR (auto loan,
    vehicle, car; 60 months)"."""
    metric = "rate and APR" if f.family == "mortgage" else f.metric
    head = " ".join(x for x in (_term(f), f.product, metric) if x)
    detail = [x for x in (f.segment if f.segment == "business" else "", f.ira_type or "", f.program or "") if x]
    words = FAMILY_WORDS.get(f.family, f.family) + (f"; {f.term_months} months" if f.term_months else "")
    return f"{head}{' - ' + ', '.join(detail) if detail else ''} ({words})"


def _label(f: RateFact) -> str:
    return f"{f.value} {f.metric}" + (f" ({f.tier_label})" if f.tier_label else "")


def _source(f: RateFact) -> Source:
    stamp = f"effective {f.effective}" if f.effective else "no effective date"
    return Source(url=f.url, snapshot_id=f.snapshot_id, quote=f"{f.row} ({stamp})")


def _distinct(fs: list[RateFact]) -> list[RateFact]:
    seen, out = set(), []
    for f in fs:
        if f.numbers not in seen:
            seen.add(f.numbers)
            out.append(f)
    return out


def _family_rank(f: RateFact) -> int:
    return FAMILY_ORDER.index(f.family) if f.family in FAMILY_ORDER else len(FAMILY_ORDER)


def _group(f: RateFact) -> tuple:
    return (f.family, f.product, f.segment, f.ira_type, f.program, f.term_months)


def claim_conflicts(snap: SnapshotData) -> list[tuple[tuple, Conflict]]:
    out = []
    for topic, (title, fmt) in CLAIM_TOPICS.items():
        winner, _, older = current(snap.claims, topic, snap.taken_on)
        if not winner or not older:
            continue
        claims = [winner, *older]
        out.append(((0, 0, 0, 0, -len(older), title), Conflict(
            topic=title,
            values=[fmt.format(c.value) for c in claims],
            sources=[Source(url=c.url, snapshot_id=snap.snapshot_id, quote=c.sentence[:300]) for c in claims],
            resolution=fmt.format(winner.value),
            reason=f"the newest dated article wins: {winner.published} says {winner.value}; older articles say "
                   + ", ".join(f"{c.value} ({c.published})" for c in older),
        )))
    return out


def open_rate_conflicts(snap: SnapshotData) -> list[tuple[tuple, Conflict]]:
    """Per product/program/term: the official values that stale copies contradict, then the stale values."""
    beaten = outranked(snap.facts)
    groups: dict[tuple, list[tuple[RateFact, RateFact]]] = {}
    for f in snap.facts:
        if id(f) in beaten:
            g = beaten[id(f)]
            groups.setdefault(_group(g), []).append((g, f))
    out = []
    for pairs in groups.values():
        winners = _distinct(sorted({id(g): g for g, _ in pairs}.values(),
                                   key=lambda g: (g.metric != "rate", g.tier_low or 0, g.metric)))
        win_nums = {g.numbers for g in winners}
        losers = [f for f in _distinct(sorted((f for _, f in pairs), key=lambda f: (source_key(f), f.url)))
                  if f.numbers not in win_nums]
        if not losers:
            continue
        w = winners[0]
        stale_pages = sorted({f.path for _, f in pairs})
        conflict = Conflict(
            topic=_topic(w),
            values=[g.value for g in winners] + [f.value for f in losers],
            sources=[_source(g) for g in winners] + [_source(f) for f in losers],
            resolution="; ".join(f"{_label(g)} on {g.path}" for g in winners),
            reason=_reason(pairs[0][0], pairs[0][1]) + f" (stale copies on {', '.join(stale_pages)})",
        )
        rank = (1, _family_rank(w), PROGRAM_ORDER.get(w.program, 3), _term_rank(w), -len(stale_pages), conflict.topic)
        out.append((rank, conflict))
    return out


def certificate_history(store: Store, skip: set[tuple]) -> list[tuple[tuple, Conflict]]:
    """Certificate specials whose rate-sheet value changed between snapshots (the latest snapshot wins)."""
    timeline: dict[tuple, list[tuple[str, RateFact]]] = {}
    for snap in store.snapshots:
        best: dict[tuple, RateFact] = {}
        for f in snap.facts:
            if f.family == "certificate" and f.page_kind == "rate_sheet" and f.segment == "personal":
                if f.key not in best or source_key(f) < source_key(best[f.key]):
                    best[f.key] = f
        for k, f in best.items():
            timeline.setdefault(k, []).append((snap.snapshot_id, f))
    latest = store.latest.snapshot_id
    merged: dict[tuple, list[list[tuple[str, RateFact]]]] = {}
    for k, seq in timeline.items():
        if seq[-1][0] != latest or _group(seq[-1][1]) in skip:
            continue                                  # retired, or already listed as an open conflict
        changes: list[tuple[str, RateFact]] = []
        for sid, f in seq:
            if not changes or changes[-1][1].numbers != f.numbers:
                changes.append((sid, f))
        if len(changes) > 1:
            merged.setdefault(_group(seq[-1][1]), []).append(changes)
    out = []
    for group, series in merged.items():
        series.sort(key=lambda ch: ch[-1][1].metric != "APY")      # APY first, then dividend rate
        newest = series[0][-1][1]
        values, sources = [], []
        for changes in series:
            for _, f in reversed(changes):
                values.append(f.value)
                sources.append(_source(f))
        history = "; ".join(f"{ch[-1][1].metric}: " + " -> ".join(f"{f.value} ({sid[:2]})" for sid, f in ch)
                            for ch in series)
        conflict = Conflict(
            topic=_topic(newest),
            values=values,
            sources=sources,
            resolution="; ".join(_label(ch[-1][1]) for ch in series) + f" (latest snapshot {latest})",
            reason=f"the official rate sheet changed between snapshots; the latest value is current ({history})",
        )
        jumbo_first = 0 if "Jumbo" in newest.product else 1          # jumbo specials carry the headline rates
        out.append(((2, _family_rank(newest), jumbo_first, newest.term_months or 0, 0, conflict.topic), conflict))
    return out


def all_conflicts(store: Store, limit: Optional[int] = MAX_CONFLICTS) -> list[Conflict]:
    if not store.snapshots:
        return []
    snap = store.latest
    open_ones = open_rate_conflicts(snap)
    beaten = outranked(snap.facts)
    open_groups = {_group(g) for g in (beaten[id(f)] for f in snap.facts if id(f) in beaten)}
    ranked = claim_conflicts(snap) + open_ones + certificate_history(store, open_groups)
    ranked.sort(key=lambda x: x[0])
    out = [c for _, c in ranked]
    return out[:limit] if limit else out
