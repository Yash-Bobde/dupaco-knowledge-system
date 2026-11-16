"""Read a member question into slots: product, term, tier, metric, segment, a date, and what kind of answer it wants.

Pure keyword rules (no LLM), so the same question always reads the same way and paraphrases that name the same
slots get the same answer.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from typing import Optional

from .pages import parse_date

# ---------------------------------------------------------------- vocabulary

# Specific products (catalog names from facts.py). Card names get their "Business ..." form when the question says business.
PRODUCT_WORDS: list[tuple[re.Pattern, str, str]] = [(re.compile(p), f, n) for p, f, n in [
    (r"share savings", "savings", "Share Savings"),
    (r"bright savings", "savings", "Bright Savings"),
    (r"you[- ]?name[- ]?it", "savings", "You Name It Savings"),
    (r"holiday club", "savings", "Holiday Club Savings"),
    (r"launch ?pad", "savings", "Launchpad Savings"),
    (r"high[- ]?yield", "savings", "High-Yield Savings"),
    (r"health savings|\bhsa\b", "hsa", "Health Savings Account"),
    (r"investor'?s choice", "money_market", "Investor's Choice Money Market"),
    (r"\bachieve\b", "money_market", "Achieve Money Market"),
    (r"(?:1st|first) rate checking", "checking", "1st Rate Checking"),
    (r"free checking", "checking", "Free Checking"),
    (r"platinum", "credit_card", "Platinum Visa"),
    (r"rewards (?:visa|card|credit)", "credit_card", "Rewards Visa"),
    (r"heloc|home equity line", "heloc", "Home Equity Line of Credit (HELOC)"),
    (r"home equity loan", "home_equity_loan", "Home Equity Loan"),
    (r"home improvement", "home_improvement", "Home Improvement Loan"),
    (r"personal loan", "personal_loan", "Personal Loan"),
    (r"\bboat\b|\brv\b|motorhome|camper", "boat_rv", "Boat & RV Loan"),
    (r"\batv\b|snowmobile|jet ?ski", "atv", "ATV, Snowmobile & Jet Ski Loan"),
    (r"rate relief", "mortgage", "Mortgage Rate Relief Program"),
]]

# Families named without a specific product. Order matters only for readability; overlaps are resolved below.
FAMILY_WORDS: list[tuple[re.Pattern, str]] = [(re.compile(p), f) for p, f in [
    (r"\bauto\b|\bcars?\b|vehicle|motorcycle|\btruck", "auto"),
    (r"mortgage|home loan|refinanc|\d+[- ]year fixed", "mortgage"),
    (r"\biras?\b|\broth\b|traditional|retirement", "ira"),
    (r"certificate|\bcds?\b|term[- ]share|\bjumbo\b(?!.*(?:mortgage|home loan))|\bmonth special\b", "certificate"),
    (r"money market", "money_market"),
    (r"checking", "checking"),
    (r"credit cards?|\bvisa\b|\bcards\b", "credit_card"),
    (r"savings", "savings"),
]]

RATE_WORDS = re.compile(r"\b(?:rates?|ap[ry]s?|dividends?|interest|yields?|earns?|pays?|paying|percent)\b|%")
NOT_ON_SITE = re.compile(r"\b(?:arm|adjustable|construction|fha|va loan|usda|land loan|lot loan)\b|\d/\d\s*arm")

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
              "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "eighteen": 18, "twenty": 20, "twenty-four": 24,
              "thirty": 30, "thirty-six": 36, "forty-eight": 48, "sixty": 60, "seventy-two": 72, "eighty-four": 84}
_TERM = re.compile(r"\b(\d{1,3}|" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True)) + r")[- ]?(months?|mo|years?|yrs?)\b")
_MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k|thousand)?\b|\b(\d+(?:\.\d+)?)\s*k\b")
_YEAR = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_MONTH = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
          r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_MONTH_YEAR = re.compile(rf"\b{_MONTH}\.?\s+(\d{{4}})\b")
_MONTH_NUM = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# ---------------------------------------------------------------- the question

@dataclass(frozen=True)
class Question:
    raw: str
    text: str                              # lowercased, normalized
    as_of: Optional[str]                   # ISO date named in the question ("as of May 2026" -> 2026-05-31)
    date_phrase: Optional[str]             # that phrase, as written ("May 2026")
    products: tuple[tuple[str, str], ...]  # (family, product) named explicitly
    families: tuple[str, ...]              # families named (including those of the products)
    segment: Optional[str]                 # "business" or None (= personal)
    ira_type: Optional[str]                # Roth / Traditional
    term_months: Optional[int]
    tier_value: Optional[float]            # a balance / loan amount, or a model year (auto, ATV)
    metric: Optional[str]                  # APY / APR / dividend rate, None = the family's default
    jumbo: bool
    special: bool
    regular: bool
    intro: bool                            # credit-card / HELOC intro or balance-transfer rate
    standard: bool                         # "standard" / "purchase APR" on a card
    refinance: Optional[str]               # refinance / cash-out refinance (mortgage)
    superlative: Optional[str]             # highest / lowest
    asked_percent: Optional[float]         # "Do any ... pay 0.35% APY?" -> 0.35
    yes_no: bool
    rate_words: bool
    not_on_site: bool                      # a product type the site has no rates for (ARM, construction, FHA...)


_DAY_DATE = re.compile(rf"\b{_MONTH}\.?\s+\d{{1,2}},\s+\d{{4}}\b")
_NUMERIC_DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")


def _date_in(text: str) -> tuple[Optional[str], Optional[str], str]:
    """(ISO date, phrase as written, text without the phrase). A month + year means the end of that month."""
    for rx in (_DAY_DATE, _NUMERIC_DATE):
        if (m := rx.search(text)) and (iso := parse_date(m.group(0))):
            return iso, m.group(0).title(), text.replace(m.group(0), " ")
    if m := _MONTH_YEAR.search(text):
        year, month = int(m.group(2)), _MONTH_NUM[m.group(1)[:3]]
        iso = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
        return iso, m.group(0).title(), text.replace(m.group(0), " ")
    return None, None, text


def read(question: str) -> Question:
    text = re.sub(r"\s+", " ", question.lower().replace("’", "'").replace("‘", "'")).strip()
    as_of, phrase, rest = _date_in(text)

    products = tuple((f, n) for p, f, n in PRODUCT_WORDS if p.search(rest))
    families = [f for p, f in FAMILY_WORDS if p.search(rest)]
    product_families = {f for f, _ in products}
    if "ira" in families:                       # "IRA certificate" is an IRA question
        families = [f for f in families if f != "certificate"]
    if product_families or len(families) > 1:   # generic "savings" only when nothing more specific is named
        families = [f for f in families if f != "savings" or "savings" in product_families]
    if "credit_card" in product_families:
        families = [f for f in families if f != "credit_card"] + ["credit_card"]
    all_families = tuple(dict.fromkeys([*[f for f, _ in products], *families]))

    term = None
    if m := _TERM.search(rest):
        n = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORDS[m.group(1)]
        term = n * 12 if m.group(2).startswith(("year", "yr")) else n

    tier: Optional[float] = None
    amounts = []
    for m in _MONEY.finditer(rest):
        raw, unit, bare_k = m.group(1), m.group(2), m.group(3)
        val = float((raw or bare_k).replace(",", "")) * (1000 if (unit or bare_k) else 1)
        amounts.append(val)
    if amounts:
        tier = (amounts[0] + amounts[1]) / 2 if len(amounts) > 1 else amounts[0]
    elif {"auto", "atv"} & set(all_families) and (y := _YEAR.search(rest)):
        tier = float(y.group(1))

    metrics = []
    if re.search(r"\bapys?\b|annual percentage yield|\byields?\b", rest):
        metrics.append("APY")
    if re.search(r"\baprs?\b|annual percentage rate", rest):
        metrics.append("APR")
    if "dividend" in rest:
        metrics.append("dividend rate")
    metric = metrics[0] if len(metrics) == 1 else None

    ira_type = "Roth" if re.search(r"\broth\b", rest) else "Traditional" if "traditional" in rest else None
    refinance = "cash-out refinance" if re.search(r"cash[- ]?out", rest) else "refinance" if "refinanc" in rest else None
    sup = ("highest" if re.search(r"\b(?:highest|best|top|maximum|max|most)\b", rest)
           else "lowest" if re.search(r"\b(?:lowest|cheapest|least)\b", rest) else None)
    pct = _PERCENT.search(rest)
    yes_no = bool(re.match(r"^(?:do|does|is|are|can|will|did)\b", rest))

    return Question(
        raw=question, text=text, as_of=as_of, date_phrase=phrase, products=products, families=all_families,
        segment="business" if re.search(r"\bbusiness", rest) else None, ira_type=ira_type, term_months=term,
        tier_value=tier, metric=metric, jumbo="jumbo" in rest, special="special" in rest,
        regular=bool(re.search(r"\bregular\b", rest)), intro=bool(re.search(r"\bintro|balance transfer", rest)),
        standard=bool(re.search(r"\bstandard\b|purchase apr", rest)), refinance=refinance, superlative=sup,
        asked_percent=float(pct.group(1)) if pct and yes_no else None, yes_no=yes_no,
        rate_words=bool(RATE_WORDS.search(rest)), not_on_site=bool(NOT_ON_SITE.search(rest)),
    )
