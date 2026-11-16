"""Decide which value is true when pages disagree (the rules NOTES.md explains).

R1  Same fact, different values: the newest "Rates are effective as of" date wins. A table with no effective date
    never beats a dated one (old promo landing pages keep undated copies of retired rates, e.g. 1st Rate Checking
    3.00% APY after the official rate became 2.50%); it is used only when it is the only source.
R2  Same date: the more official page wins: rate sheet > product page > landing page > other > calculator >
    blog/press > file. Still tied: the value more pages agree on.

The winner is chosen among facts that answer the same question: the caller filters by product, term, metric and
a tier value. A tier is matched by containment ("2025 model year" is inside both "2023–26" on a stale calculator and
"2024-27" on the rate sheet), because tier labels shift between snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from .facts import RateFact

KIND_RANK = {"rate_sheet": 0, "product": 1, "landing": 2, "home": 3, "contact": 3, "other": 3,
             "calculator": 4, "blog": 5, "press": 5, "file": 6, "junk": 9}
ANY: Any = ...  # "don't filter on this field" (None is a real value, e.g. no IRA type)


def source_key(f: RateFact) -> tuple:
    """Smaller is better: dated before undated, newer before older, official before unofficial."""
    newest_first = -date.fromisoformat(f.effective).toordinal() if f.effective else 0
    return (f.effective is None, newest_first, KIND_RANK.get(f.page_kind, 5))


def contains(f: RateFact, x: float) -> bool:
    """Does the fact's tier contain x? A fact without a tier applies to every amount."""
    if f.tier_kind is None:
        return True
    return (f.tier_low is None or f.tier_low <= x) and (f.tier_high is None or x <= f.tier_high)


def matching(facts: list[RateFact], *, family: Any = ANY, product: Any = ANY, segment: Any = ANY,
             ira_type: Any = ANY, program: Any = ANY, term_months: Any = ANY, metric: Any = ANY,
             tier_value: Optional[float] = None) -> list[RateFact]:
    """Facts that answer the same question. Fields left as ANY are not filtered."""
    want = {"family": family, "product": product, "segment": segment, "ira_type": ira_type,
            "program": program, "term_months": term_months, "metric": metric}
    out = [f for f in facts if all(v is ANY or getattr(f, k) == v for k, v in want.items())]
    if tier_value is None:
        return out
    # Tiers can overlap within one table ("$832,750 or less" ... "$100,000 or less"): a $90,000 loan sits in all
    # four, and the narrowest one applies. Done per table, so a stale table's narrower tier can't outrank a newer table.
    out = [f for f in out if contains(f, tier_value)]
    narrowest: dict[tuple, float] = {}
    for f in out:
        k = _same_question_in_table(f)
        narrowest[k] = min(narrowest.get(k, float("inf")), _width(f))
    return [f for f in out if _width(f) == narrowest[_same_question_in_table(f)]]


def _width(f: RateFact) -> float:
    low = f.tier_low if f.tier_low is not None else float("-inf")
    high = f.tier_high if f.tier_high is not None else float("inf")
    return high - low if f.tier_kind else float("inf")


def _same_question_in_table(f: RateFact) -> tuple:
    return (f.url, f.table_line, f.family, f.product, f.segment, f.ira_type, f.program, f.term_months, f.metric)


@dataclass(frozen=True)
class Pick:
    winner: RateFact
    support: tuple[RateFact, ...]   # other sources stating the winning value
    losers: tuple[RateFact, ...]    # sources stating a different value, best first
    reason: str                     # why the winner beat the best loser

    @property
    def values(self) -> list[str]:
        """Every distinct value, the winner first (as printed)."""
        seen, out = set(), []
        for f in (self.winner, *self.losers):
            if f.numbers not in seen:
                seen.add(f.numbers)
                out.append(f.value)
        return out


def _reason(w: RateFact, l: RateFact) -> str:
    if w.effective and not l.effective:
        return f"{w.path} is dated (effective {w.effective}); {l.path} has no effective date"
    if w.effective and l.effective and w.effective > l.effective:
        return f"newer effective date: {w.effective} on {w.path} vs {l.effective} on {l.path}"
    if KIND_RANK.get(w.page_kind, 5) < KIND_RANK.get(l.page_kind, 5):
        return f"same effective date; the {w.page_kind} page {w.path} outranks the {l.page_kind} page {l.path}"
    return f"same date and page type; more pages state {w.value}"


def pick(candidates: list[RateFact]) -> Optional[Pick]:
    """The winning value among facts that answer one question (R1, R2), with its support and the losers."""
    if not candidates:
        return None
    groups: dict[tuple[float, ...], list[RateFact]] = {}
    for f in candidates:
        groups.setdefault(f.numbers, []).append(f)
    for fs in groups.values():
        fs.sort(key=lambda f: (source_key(f), f.url, f.table_line))

    def group_rank(item):
        _, fs = item
        return (source_key(fs[0]), -len({f.url for f in fs}), fs[0].url)

    ordered = sorted(groups.items(), key=group_rank)
    win = ordered[0][1]
    losers = tuple(f for _, fs in ordered[1:] for f in fs)
    reason = _reason(win[0], losers[0]) if losers else "every source agrees"
    return Pick(winner=win[0], support=tuple(win[1:]), losers=losers, reason=reason)


def _question_key(f: RateFact) -> tuple:
    return (f.family, f.product, f.segment, f.ira_type, f.program, f.term_months, f.metric)


def _overlap(a: RateFact, b: RateFact) -> float:
    """How much two tiers overlap (0 = not at all). No tier on either side counts as a full match."""
    if a.tier_kind is None or b.tier_kind is None:
        return float("inf")
    if a.tier_kind != b.tier_kind:
        return 0.0
    lo = max(a.tier_low if a.tier_low is not None else -1e12, b.tier_low if b.tier_low is not None else -1e12)
    hi = min(a.tier_high if a.tier_high is not None else 1e12, b.tier_high if b.tier_high is not None else 1e12)
    return hi - lo + 1 if hi >= lo else 0.0


def _counterpart(f: RateFact, table: list[RateFact]) -> Optional[RateFact]:
    """The row of another table that states the same thing as f: the same tier if that table has it ("$200,000 or
    less" is its own row, not part of "$832,750 or less"), else the tier overlapping f's the most (a relabeled
    range: model years 2023-26 on a stale page vs 2024-27 on the rate sheet)."""
    same = [g for g in table if (g.tier_kind, g.tier_low, g.tier_high) == (f.tier_kind, f.tier_low, f.tier_high)]
    if same:
        return same[0]
    scored = [(_overlap(f, g), g) for g in table]
    scored = [x for x in scored if x[0] > 0]
    return max(scored, key=lambda x: x[0])[1] if scored else None


def outranked(facts: list[RateFact]) -> dict[int, RateFact]:
    """Stale facts: id(fact) -> the fact that beats it: its counterpart in the best table that is a strictly better
    source by R1/R2 (same product, term and metric), when that counterpart states a different value. Facts from
    equally good sources never outrank each other."""
    groups: dict[tuple, list[RateFact]] = {}
    for f in facts:
        groups.setdefault(_question_key(f), []).append(f)
    out: dict[int, RateFact] = {}
    for fs in groups.values():
        tables: dict[tuple, list[RateFact]] = {}
        for f in fs:
            tables.setdefault((f.url, f.table_line), []).append(f)
        ranked = sorted(tables.values(), key=lambda t: (min(source_key(f) for f in t), t[0].url, t[0].table_line))
        for f in fs:
            for table in ranked:
                if not min(source_key(g) for g in table) < source_key(f):
                    break
                g = _counterpart(f, table)
                if g is not None:
                    if g.numbers != f.numbers:
                        out[id(f)] = g
                    break
    return out


def stale_percents(facts: list[RateFact]) -> set[float]:
    """Rate values that only ever lose in this snapshot (a value that wins anywhere is not stale)."""
    beaten = outranked(facts)
    losing = {n for f in facts if id(f) in beaten for n in f.numbers}
    winning = {n for f in facts if id(f) not in beaten for n in f.numbers}
    return {round(n, 3) for n in losing - winning}


def winning_table(candidates: list[RateFact]) -> list[RateFact]:
    """All facts (in row order) of the single best source table among the candidates. Used when an answer lists
    several tiers or terms of one product, so tiers that only stale tables still show don't sneak in."""
    if not candidates:
        return []
    tables: dict[tuple[str, int], list[RateFact]] = {}
    for f in candidates:
        tables.setdefault((f.url, f.table_line), []).append(f)
    best = min(tables, key=lambda k: (min(source_key(f) for f in tables[k]), k))
    return tables[best]
