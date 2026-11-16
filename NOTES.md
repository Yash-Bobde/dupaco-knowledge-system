# NOTES

## The idea in one paragraph

Decide what is true **at ingest**, then answer from that decision. Ingest turns every snapshot into a
*truth table*: rate facts (one per table row and rate column, with the table's "Rates are effective as of"
date), branch records, dated company claims, links, and a search index of the site's own sentences. Fixed rules
pick a winner whenever pages disagree, and the losers are recorded (that record is `conflicts()`). At answer
time the question is read into slots (product, term, tier, metric, branch, date...). If the truth table holds
the fact, a fixed template writes the answer from the winning row, so the answer takes milliseconds and is the
same every time. Otherwise the answer comes from the best sentence on the site. If no sentence covers the
question, the system refuses with "That isn't listed on Dupaco's website. Please contact Dupaco directly for
help." and `confident=False`.

```
                    INGEST (once per snapshot, ~2 s each)
 pages ──► clean (widget noise) ──► page kind (URL) + article date
                     │
                     ├─► rate tables ──► rate facts (product, term, tier, metric, value, effective date)
                     ├─► /contact + branch-opening/closing releases ──► branches, hours, closures
                     ├─► "About Dupaco" paragraphs + Forbes releases ──► dated claims (branch count, Forbes...)
                     ├─► anchors ──► links (anchor text → live target)
                     └─► passages ──► BM25 index (passages stating a losing rate value are dropped)

                    ANSWER (one question)
 question ──► read slots + date ──► pick snapshot (as_of)
          ──► rates → branches → links → company facts → sentence search → refusal
              (first answer type that can read the question answers; each one can also refuse)
```

## How "current" is decided (the rules)

| Rule | What it says | Why |
|---|---|---|
| **R0 Snapshot** | `as_of` (or a date in the question, e.g. "as of May 2026" → May 31) uses the newest snapshot taken on or before that date. No date → the latest snapshot. | "As the website stood on that date." |
| **R1 Rates** | For the same fact (product, term, tier, metric), the **newest "Rates are effective as of" date wins**. A table with no effective date never beats a dated one; it is used only when it is the only source. | Stale copies live on calculators, landing pages and guidance pages with older stamps (5.34% vs 5.39% auto; 6.037% vs 7.13% mortgage; undated 3% vs 2.50% checking). |
| **R2 Ties** | Same date: rate sheet > product page > landing page > other > calculator > blog/press > file. Still tied: the value more pages print. | Authority only breaks ties; freshness comes from the stamp. |
| **R3 Company claims** | Branch count, Forbes count, members, assets: the **newest dated article wins**. Page counts never decide. | Old press releases keep their boilerplate: "23 branch offices" is on more pages than "22", but the newest article says 22. |
| **R4 Tiers** | A balance, loan amount or model year in the question matches the tier that contains it, by range. Within one table the narrowest tier applies. | Tier labels shift between snapshots ("2023–26" → "2024–27"), and "or less" tiers overlap. |
| **R5 Crawl noise** | The "Closed" status on /contact (live opening status at crawl time), related-article cards, phone-country dropdowns, privacy-policy modal copies are ignored. | They are page widgets, not content. |
| **R6 Dated events** | An event date is compared with the question's date: "will close March 31, 2026" means *closed* as of the latest snapshot. | Closures are announced ahead of time. |

The winner's reason is kept with every pick (e.g. "newer effective date: 2026-08-31 on /rates vs 2026-01-01 on
/switch-and-earn") and goes into `Answer.conflict_note` and `Conflict.reason`.

## How much an answer says

- A tier or amount named → that tier only. A product without a tier → each tier. "Highest/lowest" → that one.
- A family ("credit cards", "certificate rates") → every product in it; business products only when they are
  different products. Never a clarifying question back ("Which term would you like?").
- Plain "rate" → dividend rate and APY for deposits, rate and APR for mortgages (as the site prints them).
- **Numbers and URLs are copied from the evidence.** No dates or years in the text unless the question is about
  time (old values and years are the classic stale traps; they go in `conflict_note` instead).
- A recurring statistic reported in several dated articles ("294 employees used VTO in 2023" / "256 … in 2024")
  is given in every version, newest article first, each with its own year.
- Refusals carry no numbers (no phone number either), so they never state a value the site doesn't support.

## Open-ended questions (sentence search)

Pages are split into heading sections and indexed with BM25 (own code). Page kind adds a small authority
weight, and business pages count less unless the question says "business". A few banking synonyms are added
("university" ↔ "college", "CD" ↔ "certificate"). A passage that states a rate value the truth table marks as
losing is removed from the index, so old rates can't come back through search. A question asking for a rate
never goes to search at all: rates come from the rate tables or the answer is a refusal.

**Refusal gate (strict on purpose):** the passage must cover at least 60% of the question's information
(idf-weighted). The quoted sentence must mention the question's most distinctive word (in the sentence, its
heading or its page title). When the question asks for a value ("What is the late fee?"), the sentence must
contain one. A confident wrong answer costs more than "please contact us".

## LLM and API keys

**No key is needed.** Everything above works with the Python 3.11 standard library and no API key.

Optional: with `OPENAI_API_KEY` set, open-ended questions (only those, after the gate) are written by an
OpenAI model from the top search passages, in 1–3 sentences:
- The model is `OPENAI_MODEL`, default `gpt-4.1-mini`, called with temperature 0 and a fixed seed.
- The prompt says to use only the passages, copy numbers exactly, never ask back, and reply `NOT_FOUND` if the
  passages don't answer (→ refusal).
- A number in the reply that isn't in the passages → the reply is discarded and the quoted-sentence answer is used.
- A recurring statistic with several dated versions is always quoted, not sent to the model: in testing the model
  kept only the newest year, which loses the answer to "last year".
- The call has a hard 10 s deadline. Any error falls back to the quoted answer; after an auth error or 3
  failures in a row the LLM is switched off for the run.
- No LLM is used at ingest, and rate, branch and company-fact answers never use it.
- It is a paid API (OpenAI) and a small one: one short call per open-ended question.

## conflicts()

1. **Company claims:** branch count (22 vs 23/24/20/19) and Forbes ("fifth" vs "fourth"/"third"). The newest article wins.
2. **Open rate conflicts in the latest snapshot:** a stale page still prints an old value. Each stale row is
   compared with the *same* tier on the better table, or, for a relabeled range, with the tier that overlaps it
   most. Examples: 30-year mortgage on /homebuying-guidance and /mortgage-refi, auto APRs on the vehicle
   calculator and /switch-and-earn, and 1st Rate Checking on three landing pages.
3. **Certificate specials that changed between snapshots**, for example the 10-month jumbo special 3.90% → 4.00%.

Each entry lists the winning value(s) first, then the losing ones, with one source per value, the resolution and
the reason. The list is ranked (claims, then the most-asked products, then rate history) and capped at 15,
because extra entries lower precision. Repricing on every snapshot (mortgages) counts as an update, not a
contradiction, unless a stale copy is still live.

## Speed and determinism

- Ingest: about 9 s for all 5 snapshots.
- Answers: about 10 ms each without the LLM, with p95 at 0.02 s on the public set.
- No randomness: iteration is sorted, and answers are cached by (snapshot, date, normalized question), so a
  repeated question returns identical text.

## Results on the public practice set

- No API key: accuracy 0.969. All 39 scoreable items get 1.0, with 0 trap values and consistency 1.0. dup-005
  has no required tokens and is scored only by the optional judge.
- With `OPENAI_API_KEY` (`gpt-4.1-mini`): the same 0.969. Only 4 of the 40 questions reach the model; answers
  with it take p95 about 1.2 s. On held-out open-ended questions its answers were clearly better than the quoted
  sentences.
- Our own conflicts gold (7 entries built from the public traps) matches 7 of 7 with the right winners.
- Questions written outside the public set were checked by hand: rate, branch, company-fact, open-ended and
  should-refuse questions.

## Known limitations

- The question reader is keyword-based. Unusual phrasings of rate products can miss and get refused.
- Quoted answers (no key) are verbatim sentences, so they can read stiffly; the LLM path writes naturally.
- "How many branches in Iowa?" answers the total, because the site states no per-state count.
- Branch hours exist only in branch-opening press releases. A branch with no published hours is refused
  rather than given another branch's hours.

## Layout

`solution.py` (build + the 3 methods) and the `dkb/` package:

| Module | What it does |
|---|---|
| `pages` | Cleaning, page kinds and dates. |
| `tables` | Rate tables and their effective dates. |
| `facts` | Rate facts. |
| `truth` | Winners, losers and stale facts. |
| `store` | Per-snapshot data and `as_of`. |
| `branches` | Branch records and closures. |
| `claims` | Dated claims, routing number, phone and the join page. |
| `links` | Anchor text and live targets. |
| `search` | Passages and BM25. |
| `question` | Slot reader. |
| `answer` | The router. |
| `answer_*` | One module per answer type. |
| `reply` | The refusal and the final checks. |
| `llm` | The optional OpenAI call. |
| `conflicts` | The conflicts list. |

The snapshot data is not in this repository.
