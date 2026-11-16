"""Rate answers, written from the truth table with fixed templates: values and labels are copied from the winning
table row, nothing is invented, and the same slots always give the same text.

How much to say (approved rules):
- a tier or amount named -> that tier only; a product without a tier -> each tier, from the lowest amount up;
- "highest"/"lowest" -> that one tier; a family ("credit cards", "certificate rates") -> every product in it,
  adding business products only when they are different products (Business Platinum Visa), and only products
  that still have an official source (a product left only on a stale page is not listed);
- plain "rate": deposits give dividend rate and APY, mortgages give rate and APR; a named metric gives only that;
- a mortgage without purchase/refinance means purchase; personal unless the question says business;
- no dates in the text unless the question names one (the effective date goes in the sources and conflict note).
"""
from __future__ import annotations

import re
from typing import Optional

from .facts import LOAN_FAMILIES, RateFact
from .question import Question
from .reply import LINK_WORDS, refusal
from .shapes import Answer, Source
from .store import SnapshotData
from .truth import ANY, Pick, matching, pick, source_key, winning_table

DEPOSIT_FAMILIES = {"savings", "money_market", "certificate", "ira", "hsa", "checking"}
OFFER_Q = re.compile(r"\b(?:offer|offers|provide|provides|sell|available)\b")
# An "offer" question gets the rate list only when it names a product family by its noun ("business credit cards"),
# not through a loose word ("student loan refinancing" is not a mortgage refinance).
OFFER_NOUN = re.compile(r"\b(?:credit cards?|visa|checking|savings|certificates?|cds?|money markets?|iras?|auto loans?|"
                        r"car loans?|mortgages?|home equity|heloc)\b")
OFFER_OTHER = re.compile(r"\bstudent|\binsurance|\bbusiness loans?\b")
OFFICIAL_KINDS = {"rate_sheet", "product"}
FAMILY_DEFAULT = {"savings": "Share Savings", "money_market": "Investor's Choice Money Market",
                  "mortgage": "Fixed Conventional Mortgage"}
_GENERIC = {"loan", "loans", "account", "accounts", "rate", "rates", "the", "and", "of", "fixed", "savings", "visa",
            "money", "market", "ira", "certificate"}

# ---------------------------------------------------------------- which products the question is about

def _metrics(family: str, q: Question) -> list[str]:
    if q.metric:
        return [q.metric]
    if family in DEPOSIT_FAMILIES:
        return ["dividend rate", "APY"]
    return ["rate", "APR"] if family == "mortgage" else ["APR"]


def _program(family: str, q: Question):
    if family == "mortgage":
        return q.refinance or "purchase"
    if family in ("credit_card", "heloc"):
        return "intro" if q.intro else "standard" if q.standard else ANY
    return ANY


def _scopes(q: Question, facts: list[RateFact]) -> list[dict]:
    """The (family, product, segment, ...) groups the answer covers, in a stable order."""
    segment = q.segment or "personal"
    present: dict[tuple[str, str, str], int] = {}       # (family, product, segment) -> first position
    official: set[tuple[str, str, str]] = set()
    for i, f in enumerate(facts):
        k = (f.family, f.product, f.segment)
        present.setdefault(k, i)
        if f.page_kind in OFFICIAL_KINDS:
            official.add(k)

    def scope(family, product, seg):
        return {"family": family, "product": product, "segment": seg, "program": _program(family, q),
                "ira_type": q.ira_type if family == "ira" and q.ira_type else ANY}

    if q.products:
        out = []
        for family, product in q.products:
            if family == "credit_card" and q.segment == "business":
                product = f"Business {product}"
            seg = "business" if product.startswith("Business ") else segment
            out.append(scope(family, product, seg))
        return out

    out = []
    for family in q.families:
        names = sorted({(p, s) for (fam, p, s) in present if fam == family and s == segment},
                       key=lambda ps: present[(family, *ps)])
        if family == "certificate" or family == "ira":
            if q.jumbo:
                names = [ps for ps in names if "Jumbo" in ps[0]]
            elif q.special:
                names = [ps for ps in names if "Special" in ps[0]]
            elif q.regular:
                names = [ps for ps in names if "Regular" in ps[0]]
        single_intent = q.term_months is not None or q.tier_value is not None or q.superlative or q.yes_no
        if family in FAMILY_DEFAULT and (single_intent or family == "mortgage"):
            default = "Mortgage Rate Relief Program" if family == "mortgage" and "rate relief" in q.text else FAMILY_DEFAULT[family]
            names = [ps for ps in names if ps[0] == default]
        elif not single_intent:
            # A family list: only products that still have an official source; add business products that differ.
            names = [ps for ps in names if (family, *ps) in official]
            if q.segment is None:
                personal = {p for p, _ in names}
                names += sorted({(p, s) for (fam, p, s) in official if fam == family and s == "business"
                                 and p not in personal}, key=lambda ps: present[(family, *ps)])
        out += [scope(family, p, s) for p, s in names]
    return out


# ---------------------------------------------------------------- rows, labels and values

def _rep(f: RateFact) -> Optional[float]:
    """A value inside the fact's tier, used to find competing facts for the same tier on other pages."""
    if f.tier_kind is None:
        return None
    if f.tier_low in (None, 0.0):
        return f.tier_high if f.tier_high is not None else 0.0    # "$0 or more": any amount, so 0 is inside
    return f.tier_low if f.tier_high is None else (f.tier_low + f.tier_high) / 2


def _row_pick(r: RateFact, facts: list[RateFact]) -> Pick:
    cands = matching(facts, family=r.family, product=r.product, segment=r.segment, ira_type=r.ira_type,
                     program=r.program, term_months=r.term_months, metric=r.metric, tier_value=_rep(r))
    return pick(cands) or Pick(r, (), (), "only source")


def _rows(scope: dict, q: Question, metric: str, facts: list[RateFact]) -> list[RateFact]:
    cands = matching(facts, **scope, term_months=q.term_months if q.term_months else ANY,
                     metric=metric, tier_value=q.tier_value)
    if not cands:
        return []
    if q.tier_value is not None:
        return [pick(cands).winner]
    rows = winning_table(cands)
    term_order = {t: i for i, t in enumerate(dict.fromkeys(r.term_months for r in rows))}
    rows = sorted(rows, key=lambda r: (term_order[r.term_months], r.ira_type or "",
                                       r.tier_low if r.tier_low is not None else float("-inf"),
                                       r.tier_high if r.tier_high is not None else float("inf")))
    if q.superlative:
        best = max if q.superlative == "highest" else min
        return [best(rows, key=lambda r: r.numbers)]
    return rows


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in _GENERIC}


def _name_details(r: RateFact) -> tuple[str, list[str]]:
    """The row's own name when its first cell names the product ("17 Month Fixed Certificate Special"), else the
    catalog name; plus the remaining descriptive cells (tier, term) as printed."""
    product = r.product if r.segment == "personal" or r.product.startswith("Business") else f"Business {r.product}"
    desc = [d for d in (d.replace("*", "").strip() for d in r.desc) if d]
    if desc and _words(desc[0]) & _words(product):
        return desc[0], desc[1:]
    return product, desc


def _label(r: RateFact) -> str:
    """"Share Savings ($10,000 – $24,999.99)", "Auto & Motorcycle Loan (2024-27 Model Year, 60 Months)"."""
    name, details = _name_details(r)
    return f"{name} ({', '.join(details)})" if details else name


def _details(r: RateFact) -> str:
    """" ($25,000+)" / " (2024-27 Model Year, 48 Months)" / "" for the highest-lowest and yes-no sentences."""
    _, details = _name_details(r)
    return f" ({', '.join(details)})" if details else ""


def _companions(r: RateFact, metrics: list[str], facts_by_row: dict) -> list[RateFact]:
    """The same table row's facts for each wanted metric (dividend rate + APY, rate + APR), in metric order."""
    same = facts_by_row.get((r.url, r.table_line, r.row), {})
    return [same[m] for m in metrics if m in same]


def _values(fs: list[RateFact]) -> str:
    return ", ".join(f"{f.value} {f.metric}" for f in fs)


# ---------------------------------------------------------------- the answer

def rate_answer(q: Question, snap: SnapshotData) -> Optional[Answer]:
    """Answer a rate question from the truth table, or None if this isn't a rate question we can read.
    Link questions ("what link shows certificate rates?") are left to the link answers."""
    offer = (q.yes_no and not q.rate_words and bool(OFFER_Q.search(q.text)) and bool(OFFER_NOUN.search(q.text))
             and not OFFER_OTHER.search(q.text))                          # "Does Dupaco offer business credit cards?"
    if not q.families or not (q.rate_words or offer) or LINK_WORDS.search(q.text):
        return None
    if q.not_on_site:
        return refusal()
    facts = snap.facts
    facts_by_row: dict[tuple, dict[str, RateFact]] = {}
    for f in facts:
        facts_by_row.setdefault((f.url, f.table_line, f.row), {})[f.metric] = f

    lines: list[tuple[RateFact, list[RateFact]]] = []
    picks: list[Pick] = []
    for scope in _scopes(q, facts):
        metrics = _metrics(scope["family"], q)
        rows = _rows(scope, q, metrics[0], facts)
        if not rows and len(metrics) > 1:
            metrics = metrics[1:]
            rows = _rows(scope, q, metrics[0], facts)
        for r in rows:
            picks.append(_row_pick(r, facts))
            lines.append((r, _companions(r, metrics, facts_by_row) or [r]))
    if not lines:
        return refusal()

    if q.yes_no and q.asked_percent is not None:
        hit = [(r, fs) for r, fs in lines if any(q.asked_percent in f.numbers for f in fs)]
        if hit:
            r, fs = hit[0]
            text = f"Yes. {_label(r)} pays {_values(fs)}."
        else:
            loan = lines[0][0].family in LOAN_FAMILIES     # a borrower wants the lowest rate, a saver the highest
            r, fs = (min if loan else max)(lines, key=lambda x: x[1][-1].numbers)
            text = f"No. {r.product} {'starts at' if loan else 'tops out at'} {_values([fs[-1]])}{_details(r)}."
    elif q.superlative and len(lines) == 1:
        r, fs = lines[0]
        text = f"The {q.superlative} {r.product} {fs[-1].metric} is {fs[-1].value}{_details(r)}."
    else:
        text = " ".join(f"{_label(r)}: {_values(fs)}." for r, fs in lines)
        if offer:
            text = f"Yes. {text}"
    if q.date_phrase and text[:3] not in ("Yes", "No."):
        text = f"As of {q.date_phrase}: {text}"

    return Answer(text=text, sources=_sources(picks, snap), confident=True, conflict_note=_conflict_note(picks))


def _sources(picks: list[Pick], snap: SnapshotData, limit: int = 5) -> list[Source]:
    """Winning pages first, then pages that agree; one entry per URL; quote = the row + its effective stamp."""
    ordered: list[RateFact] = [p.winner for p in picks]
    ordered += sorted((f for p in picks for f in p.support), key=lambda f: (source_key(f), f.url))
    out, seen = [], set()
    for f in ordered:
        if f.url in seen:
            continue
        seen.add(f.url)
        stamp = f.effective_line or "no effective date on this table"
        out.append(Source(url=f.url, snapshot_id=snap.snapshot_id, quote=f"{f.row} ({stamp})"))
        if len(out) == limit:
            break
    return out


def _conflict_note(picks: list[Pick], limit: int = 3) -> Optional[str]:
    notes = []
    for p in picks:
        if p.losers:
            loser = p.losers[0]
            note = f"{p.winner.value} chosen over {loser.value} ({loser.path}): {p.reason}."
            if note not in notes:
                notes.append(note)
    return " ".join(notes[:limit]) or None
