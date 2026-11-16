"""Optional OpenAI call that turns the top search passages into a short answer (open-ended questions only).

- The key is read only from the OPENAI_API_KEY environment variable; the model from OPENAI_MODEL (default below).
- Standard library only (urllib): nothing to install, and a hard deadline on every call, because the harness runs
  questions on one worker thread and a hung call would delay every question behind it.
- temperature 0 and a fixed seed, so a repeated question gets the same words; answers are cached by the router.
- Any error, timeout or refusal from the API returns None, and the caller falls back to quoted sentences.
  After an auth error or 3 failures in a row the LLM is switched off for the rest of the run.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Optional

NOT_FOUND = "NOT_FOUND"
DEFAULT_MODEL = "gpt-4.1-mini"
DEADLINE_S = 10.0
_URL = "https://api.openai.com/v1/chat/completions"
_state = {"failures": 0, "off": False}

SYSTEM = """You answer questions from members of Dupaco Community Credit Union, using ONLY the website excerpts given.
Rules:
1. Answer directly in 1-3 short sentences. Start with "Yes" or "No" when the question is a yes/no question.
2. Copy every number, dollar amount, rate, date, phone number, product name and URL exactly as the excerpts write it. Never calculate, round or guess.
3. If the excerpts do not clearly answer the question, reply with exactly NOT_FOUND and nothing else.
4. If excerpts report a figure for different years, give the most recent one first with its year, then the earlier ones with their years.
5. Never ask the member a question, never ask for personal details (such as a ZIP code or member number), and never mention excerpts, sources, passages or context.
6. Plain text only: no markdown, no bullet points."""


def available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY")) and not _state["off"]


def model() -> str:
    return os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL


def _prompt(question: str, passages: list[tuple[str, str, Optional[str], str]]) -> str:
    parts = []
    for i, (title, url, published, text) in enumerate(passages, 1):
        dated = f", published {published}" if published else ""
        parts.append(f"[{i}] {title} ({url}{dated})\n{text}")
    return "Website excerpts:\n\n" + "\n\n".join(parts) + f"\n\nMember question: {question}"


def _call(body: bytes, key: str, out: dict) -> None:
    req = urllib.request.Request(_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=DEADLINE_S) as resp:
            out["data"] = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        out["status"] = e.code
    except Exception as e:  # network error, timeout, bad JSON
        out["error"] = type(e).__name__


def answer(question: str, passages: list[tuple[str, str, Optional[str], str]]) -> Optional[str]:
    """The model's answer, NOT_FOUND, or None when the call failed (the caller then falls back)."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key or _state["off"]:
        return None
    body = json.dumps({
        "model": model(), "temperature": 0, "seed": 7, "max_tokens": 220,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": _prompt(question, passages)}],
    }).encode("utf-8")
    out: dict = {}
    worker = threading.Thread(target=_call, args=(body, key, out), daemon=True)
    worker.start()
    worker.join(DEADLINE_S + 1)
    try:
        text = out["data"]["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        _state["failures"] += 1
        if out.get("status") in (401, 403, 404) or _state["failures"] >= 3:
            _state["off"] = True
        return None
    _state["failures"] = 0
    if NOT_FOUND in text or not text:
        return NOT_FOUND
    return text
