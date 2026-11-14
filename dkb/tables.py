"""Find the markdown tables on a cleaned page, with the label lines above them and their effective date.

What the site does (see the page-format notes):
- Section titles are plain lines, not headings ("Savings Accounts" / "Share Savings" / table), and some tables
  have no product column, so the labels above a table are kept with it.
- Headers can be wrong (the vehicle calculator's auto table is headed "Account Type | Balance Tier | ..."), so
  each column is also typed from its cells.
- Footnote digits are glued onto values ("0.20%1", "5.39% APR1") and are split off here.
- The effective-date stamp follows the table but not always on the next line, and a footnote below it can carry
  an older date, so a table takes the FIRST stamp after it and before the next table.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from .pages import effective_date

_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_GLUED_FOOTNOTE = re.compile(r"(%|\)|APR|APY)\s?[0-9¹²³]$")  # "4.10%2" -> "4.10%", "15.50% 1", "(APY)1" -> "(APY)"
_MD_ESCAPE = re.compile(r"\\([\\*#_$\[\]()|-])")


def clean_cell(cell: str) -> str:
    """Cell text as printed, minus markdown escapes, bold markers and a glued footnote digit."""
    s = _MD_ESCAPE.sub(r"\1", cell).replace("**", "").strip()
    return _GLUED_FOOTNOTE.sub(r"\1", s)


# ---------------------------------------------------------------- column types

_EMPTY = {"", "—", "–", "-", "n/a", "N/A"}
_PERCENT = re.compile(r"\d\s*%")
_TERM = re.compile(r"^\d+\s*(?:months?|mo\.?|years?|yr)\b", re.I)
_MODEL_YEAR = re.compile(r"model year|^(?:19|20)\d\d\s*[-–]\s*\d{2,4}$", re.I)
_NUMBER = re.compile(r"^[\d.,]+$")
_BARE_RATE = re.compile(r"^\d{1,2}\.\d{1,3}$")                     # "7.29" (01 Boat & RV prints rates without %)
_RATE_HEADER = re.compile(r"\b(?:rate|apr|apy|yield|dividend)\b", re.I)


def cell_type(cell: str) -> str:
    if cell in _EMPTY:
        return "empty"
    if _PERCENT.search(cell):
        return "percent"
    if _MODEL_YEAR.search(cell):
        return "model_year"
    if _TERM.match(cell):
        return "term"
    if cell.lstrip().startswith("$"):
        return "money"
    if _NUMBER.match(cell):
        return "number"
    return "text"


def column_types(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> tuple[str, ...]:
    """Most common non-empty cell type per column (ties go to the type seen first). A column of bare decimals
    under a rate-like header ("Annual Percentage Rate", "Dividend Rate") counts as a percent column."""
    out = []
    for i, head in enumerate(header):
        cells = [r[i] for r in rows if i < len(r)]
        kinds = [k for k in map(cell_type, cells) if k != "empty"]
        kind = Counter(kinds).most_common(1)[0][0] if kinds else "empty"
        if kind == "number" and _RATE_HEADER.search(head) and all(
                _BARE_RATE.match(c) for c in cells if cell_type(c) == "number"):
            kind = "percent"
        out.append(kind)
    return tuple(out)


# ---------------------------------------------------------------- labels above a table

_LINK_ONLY = re.compile(r"^(?:\[[^\]]*\]\([^)]*\)\s*)+$")
_LIST_LINK = re.compile(r"^- \[([^\]]+)\]\([^)]*\)$")      # 01-style category: "- [Savings Account Rates](...rates#)"
_FOOTNOTE_LINE = re.compile(r"^(?:[1-9¹²³](?=[A-Z])|\\\*|\*(?!\*))")  # "1Rates are...", "\*Payment example..."
_HEADING = re.compile(r"^#{1,6}\s+")


def _label(line: str) -> Optional[str]:
    """The line as a short label ("Share Savings", "Home Loans - Purchase"), or None for links, footnotes,
    stamps and sentences."""
    s = line.strip()
    if not s or s.startswith(("|", "!", ">")):
        return None
    if m := _LIST_LINK.match(s):
        return m.group(1).strip()
    if _LINK_ONLY.match(s) or _FOOTNOTE_LINE.match(s) or effective_date(s):
        return None
    s = _HEADING.sub("", s).replace("**", "").strip()
    if not s or len(s) > 80 or s.endswith((".", ":", "!", "?")):
        return None
    return s


# ---------------------------------------------------------------- tables

@dataclass(frozen=True)
class Table:
    line: int                               # 1-based line of the header row in the cleaned text
    heading: Optional[str]                  # nearest markdown heading above the table ("Home Rates")
    labels: tuple[str, ...]                 # up to 3 label lines since the previous table, top to bottom
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    columns: tuple[str, ...]                # per column: percent, money, term, model_year, number, text, empty
    effective: Optional[str]                # ISO date of the first effective stamp after the table
    effective_line: Optional[str]           # that stamp line, verbatim (used later as evidence)

    @property
    def is_rate_table(self) -> bool:
        return "percent" in self.columns


def _cells(line: str) -> tuple[str, ...]:
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return tuple(clean_cell(c) for c in _CELL_SPLIT.split(inner))


def find_tables(text: str) -> list[Table]:
    """Every markdown table (header row + separator row + body rows) on the page, in page order."""
    lines = text.split("\n")
    blocks = []  # (start, end) line indexes of each table, end exclusive
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            # A table with an all-empty header is page layout (bullet lists in cells), not data.
            if j - i >= 2 and _SEPARATOR.match(lines[i + 1].strip()) and any(_cells(lines[i])):
                blocks.append((i, j))
            i = j
        else:
            i += 1

    tables = []
    heading: Optional[str] = None
    prev_end = 0
    for n, (start, end) in enumerate(blocks):
        between = lines[prev_end:start]
        for line in between:
            if _HEADING.match(line.strip()):
                heading = _HEADING.sub("", line.strip()).replace("**", "").strip() or heading
        labels: list[str] = []
        for line in between:
            lab = _label(line)
            if lab and (not labels or labels[-1] != lab):
                labels.append(lab)

        next_start = blocks[n + 1][0] if n + 1 < len(blocks) else len(lines)
        eff, eff_line = None, None
        for line in lines[end:next_start]:
            if d := effective_date(line):
                eff, eff_line = d, line.strip()
                break

        header = _cells(lines[start])
        rows = tuple(r + ("",) * (len(header) - len(r)) for r in (_cells(l) for l in lines[start + 2:end]))
        tables.append(Table(
            line=start + 1, heading=heading, labels=tuple(labels[-3:]), header=header, rows=rows,
            columns=column_types(header, list(rows)), effective=eff, effective_line=eff_line,
        ))
        prev_end = end
    return tables
