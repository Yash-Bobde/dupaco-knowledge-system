"""Turn rate-table rows into rate facts: one fact per (row, rate column).

A fact reads like: "on /rates in snapshot 05, the personal Auto & Motorcycle Loan, 60 months, model years
2024-2027, APR = 5.39%, effective 2026-08-31". Which value is *true* is decided later (truth.py); here every
row on every page becomes a fact, stale copies included, so they can be compared.

The same product is named differently across pages and snapshots ("Share Savings Account" vs "Share Savings",
"Investor’s Choice" vs "Investor's Choice Money Market (Business)"), and some tables name the product only in
a label above the table. So products are matched against a small catalog of Dupaco's rate products: the row's
first cell first, then the labels above the table (nearest first), then the nearest heading.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .pages import CleanPage
from .tables import Table

# ---------------------------------------------------------------- product catalog

# (pattern, family, product). Order matters: the first hit wins, so specific patterns come first.
# Patterns run on lowercased text with curly apostrophes straightened.
CATALOG: list[tuple[re.Pattern, str, str]] = [(re.compile(p), f, n) for p, f, n in [
    (r"^(?:roth|traditional):", "ira", "IRA"),                       # refined below (special / regular / variable)
    (r"(?:purchase|refinance):|fixed conventional|rate relief", "mortgage", "Fixed Conventional Mortgage"),
    (r"health savings|\bhsa\b", "hsa", "Health Savings Account"),
    (r"launchpad", "savings", "Launchpad Savings"),
    (r"bright savings", "savings", "Bright Savings"),
    (r"you[- ]name[- ]it", "savings", "You Name It Savings"),
    (r"holiday club", "savings", "Holiday Club Savings"),
    (r"high[- ]yield savings", "savings", "High-Yield Savings"),
    (r"share savings", "savings", "Share Savings"),
    (r"investor's choice", "money_market", "Investor's Choice Money Market"),
    (r"achieve", "money_market", "Achieve Money Market"),
    (r"1st rate checking", "checking", "1st Rate Checking"),
    (r"free checking", "checking", "Free Checking"),
    (r"certificate special", "certificate", "Fixed Certificate Special"),  # refined below (jumbo or not)
    (r"^regular(?: business)?(?: certificates?)?$", "certificate", "Regular Certificate"),
    (r"business platinum visa", "credit_card", "Business Platinum Visa"),
    (r"business rewards visa", "credit_card", "Business Rewards Visa"),
    (r"platinum (?:visa|credit card)", "credit_card", "Platinum Visa"),
    (r"rewards (?:visa|credit card)", "credit_card", "Rewards Visa"),
    (r"heloc|line of credit", "heloc", "Home Equity Line of Credit (HELOC)"),
    (r"home equity loan|^loan [–-] standard", "home_equity_loan", "Home Equity Loan"),
    (r"home improvement", "home_improvement", "Home Improvement Loan"),
    (r"personal loan", "personal_loan", "Personal Loan"),
    (r"\batv\b|snowmobile|jet ski", "atv", "ATV, Snowmobile & Jet Ski Loan"),
    (r"\bboat\b|\brv\b", "boat_rv", "Boat & RV Loan"),
    (r"\bauto\b|motorcycle|vehicle", "auto", "Auto & Motorcycle Loan"),
]]

LOAN_FAMILIES = {"mortgage", "credit_card", "heloc", "home_equity_loan", "home_improvement", "personal_loan",
                 "atv", "boat_rv", "auto"}


def _norm(s: str) -> str:
    return s.lower().replace("’", "'").replace("‘", "'").strip()


def identify(first_cell: str, labels: tuple[str, ...], heading: Optional[str]) -> tuple[str, str, str]:
    """(family, product, the text it was matched on); ("unknown", label, "") when nothing matches."""
    for source in (first_cell, *reversed(labels), heading or ""):
        text = _norm(source)
        for pattern, family, product in CATALOG:
            if text and pattern.search(text):
                return family, product, text
    return "unknown", (labels[-1] if labels else first_cell), ""


# ---------------------------------------------------------------- row details

_TERM_IN = re.compile(r"(\d+)\s*-?\s*(months?|mo\b|years?|yr)", re.I)
_MODEL_YEARS = re.compile(r"((?:19|20)\d\d)\s*[-–]\s*(\d{2,4})")
_YEAR = re.compile(r"(?:19|20)\d\d")
_AMOUNT = re.compile(r"\d[\d,]*(?:\.\d+)?")
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_BARE = re.compile(r"^\d{1,2}(?:\.\d{1,3})?$")
_SUFFIX = re.compile(r"\s*\b(?:APR|APY)\b\*?\s*$", re.I)


def term_months(cells: list[str]) -> Optional[int]:
    """First term found in the cells: "60 Months" -> 60, "30-Year" -> 360, "(60 mo)" -> 60, "5 Years" -> 60."""
    for c in cells:
        if m := _TERM_IN.search(c):
            n = int(m.group(1))
            return n * 12 if m.group(2).lower().startswith(("year", "yr")) else n
    return None


def model_year_range(s: str) -> Optional[tuple[Optional[int], Optional[int]]]:
    """"2024-27 Model Year" -> (2024, 2027); "Model Year 2026-23" -> (2023, 2026); "2016 ... (and older)" -> (None, 2016)."""
    if m := _MODEL_YEARS.search(s):
        a, b = int(m.group(1)), int(m.group(2))
        b = a // 100 * 100 + b if b < 100 else b
        return min(a, b), max(a, b)
    if m := _YEAR.search(s):
        y = int(m.group(0))
        low = s.lower()
        return (None, y) if "older" in low else (y, None) if "newer" in low else (y, y)
    return None


def money_range(s: str) -> Optional[tuple[Optional[float], Optional[float]]]:
    """"$10,000 – $24,999.99" -> (10000, 24999.99); "$25,000+" -> (25000, None); "$10,000-24,999" ->
    (10000, 24999); "... $100,000 or less" -> (0, 100000); "Roth: $15,000+" -> (15000, None)."""
    if "$" not in s:
        return None
    tail = s[s.index("$"):]
    nums = [float(x.replace(",", "")) for x in _AMOUNT.findall(tail)]
    if not nums:
        return None
    low = tail.lower()
    if "or less" in low:
        return 0.0, nums[0]
    if "+" in tail or "or more" in low:
        return nums[0], None
    return (nums[0], nums[1]) if len(nums) > 1 else (nums[0], nums[0])


def metric_for(header: str, cell: str) -> Optional[str]:
    """What a rate column measures. A cell's own "APR"/"APY" suffix beats the header (the vehicle calculator's
    header says "Annual Percentage Yield" over APR values). Points columns are not rates."""
    h = _norm(header)
    if re.search(r"\bAPR\b", cell):
        base = "APR"
    elif re.search(r"\bAPY\b", cell):
        base = "APY"
    elif "point" in h:
        return None
    elif "yield" in h or re.search(r"\bapy\b", h):
        base = "APY"
    elif "dividend" in h:
        base = "dividend rate"
    elif "annual percentage rate" in h or re.search(r"\bapr\b", h):
        base = "APR"
    elif "rate" in h:
        base = "rate"
    else:
        return None
    if re.search(r"(?:1st|first) 6 months", h):
        return f"{base} (first 6 months)"
    if "after promotional" in h:
        return f"{base} (after promotional period)"
    return base


def parse_value(cell: str) -> tuple[str, tuple[float, ...]]:
    """("5.39%", (5.39,)) from "5.39% APR"; ("12.50% – 19.50%", (12.5, 19.5)); ("7.29", (7.29,)); ("", ()) if none."""
    value = _SUFFIX.sub("", cell).strip()
    nums = tuple(float(x) for x in _PCT.findall(value))
    if not nums and _BARE.match(value):
        nums = (float(value),)
    return (value, nums) if nums else ("", ())


# ---------------------------------------------------------------- the fact

@dataclass(frozen=True)
class RateFact:
    snapshot_id: str
    url: str
    path: str
    page_kind: str
    family: str                       # savings, money_market, certificate, ira, hsa, checking, auto, credit_card, ...
    product: str                      # catalog name, e.g. "Fixed Jumbo Certificate Special"
    segment: str                      # personal | business
    ira_type: Optional[str]           # Roth | Traditional
    program: Optional[str]            # mortgage: purchase / refinance / cash-out refinance / rate relief; cards & HELOC: intro / standard
    term_months: Optional[int]
    tier_kind: Optional[str]          # balance | loan_amount | model_year
    tier_low: Optional[float]
    tier_high: Optional[float]
    tier_label: Optional[str]         # the tier as printed ("$10,000 – $24,999.99", "2024-27 Model Year")
    metric: str                       # APY, dividend rate, APR, rate (+ promo qualifiers)
    value: str                        # as printed, e.g. "5.39%"
    numbers: tuple[float, ...]        # (5.39,) — compare values by these, not by text ("7.250%" == "7.25%")
    effective: Optional[str]          # ISO date of the table's "Rates are effective as of" stamp
    effective_line: Optional[str]
    row: str                          # the table row, verbatim (used as the quote)
    labels: tuple[str, ...]
    table_line: int

    @property
    def key(self) -> tuple:
        """What the fact is about (everything except where it was seen and what it says)."""
        return (self.family, self.product, self.segment, self.ira_type, self.program, self.term_months,
                self.tier_kind, self.tier_low, self.tier_high, self.metric)


def _refine(family: str, product: str, first: str, labels: tuple[str, ...]) -> tuple[str, Optional[str], Optional[str]]:
    """(product, ira_type, program) using details in the row text."""
    low = _norm(first)
    ira_type = program = None
    if family == "ira":
        m = re.match(r"^(roth|traditional):\s*(.*)$", low)
        ira_type, rest = m.group(1).title(), m.group(2)
        if "special" in rest:
            product = "Fixed Jumbo IRA Special" if "jumbo" in rest else "Fixed IRA Special"
        elif "$" in rest:
            product = "Variable IRA"
        else:
            product = "Regular IRA"
    elif family == "certificate" and product == "Fixed Certificate Special" and "jumbo" in low:
        product = "Fixed Jumbo Certificate Special"
    elif family == "mortgage":
        if "rate relief" in low:
            product, program = "Mortgage Rate Relief Program", "rate relief"
        elif low.startswith("purchase"):
            program = "purchase"
        elif low.startswith("refinance"):
            cash_out = any(re.search(r"cash|home equity", _norm(l)) for l in labels)
            program = "cash-out refinance" if cash_out else "refinance"
    elif family in ("credit_card", "heloc"):
        program = "intro" if "intro" in low else "standard"
    return product, ira_type, program


def rate_facts(snapshot_id: str, page: CleanPage, tables: list[Table]) -> list[RateFact]:
    """Every (row, rate column) of every rate table on the page as a RateFact (family "unknown" if unmatched)."""
    out: list[RateFact] = []
    for t in tables:
        if not t.is_rate_table:
            continue
        rate_cols = [i for i, kind in enumerate(t.columns) if kind == "percent"]
        tier_col = next((i for i, kind in enumerate(t.columns)
                         if kind == "money" and not re.search(r"\bmin|\bmax|payment", t.header[i], re.I)), None)
        for row in t.rows:
            first = row[0]
            family, product, matched = identify(first, t.labels, t.heading)
            product, ira_type, program = _refine(family, product, first, t.labels)
            segment = "business" if page.path.startswith("/business") or "business" in matched else "personal"
            other_cells = [c for i, c in enumerate(row) if i not in rate_cols]

            tier_kind = tier_label = None
            tier: Optional[tuple] = None
            if family in ("auto", "atv"):
                cell = next((c for c in other_cells if model_year_range(c)), None)
                if cell:
                    tier_kind, tier_label, tier = "model_year", cell, model_year_range(cell)
            else:
                cell = row[tier_col] if tier_col is not None and "$" in row[tier_col] else (first if "$" in first else None)
                if cell and (rng := money_range(cell)):
                    tier_kind = "loan_amount" if family in LOAN_FAMILIES else "balance"
                    tier_label = cell[cell.index("$"):] if family in ("ira", "mortgage") else cell
                    tier = rng

            for i in rate_cols:
                metric = metric_for(t.header[i], row[i])
                value, numbers = parse_value(row[i])
                if not metric or not numbers:
                    continue
                out.append(RateFact(
                    snapshot_id=snapshot_id, url=page.url, path=page.path, page_kind=page.kind,
                    family=family, product=product, segment=segment, ira_type=ira_type, program=program,
                    term_months=term_months(other_cells), tier_kind=tier_kind,
                    tier_low=tier[0] if tier else None, tier_high=tier[1] if tier else None, tier_label=tier_label,
                    metric=metric, value=value, numbers=numbers, effective=t.effective,
                    effective_line=t.effective_line, row=" | ".join(row), labels=t.labels, table_line=t.line,
                ))
    return out
