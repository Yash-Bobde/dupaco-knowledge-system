# Dupaco knowledge system

A system that reads five dated snapshots of a credit union's website and answers member
questions with the current value (or the value as of a date), the right tier, cited sources,
a list of the site's contradictions, and an honest "isn't listed" when the site doesn't say.

- Entry point: `solution.py` → `build()` returns an object with `ingest`, `answer` and `conflicts`.
- Python 3.11, standard library only (`requirements.txt` has nothing to install).
- No API key needed. Optional: set `OPENAI_API_KEY` (and optionally `OPENAI_MODEL`) to have an LLM
  write the open-ended answers; everything else is rule-based.
- How it works and how "current" is decided: see [`NOTES.md`](NOTES.md).

Run the kit's scorer from the kit folder (on Windows set `PYTHONUTF8=1` first):

```
python harness/run_eval.py --submission dupaco-knowledge-system --eval eval/public_eval.jsonl
```

The snapshot data is not included in this repository.
