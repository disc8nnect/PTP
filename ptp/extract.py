"""Visit Notes: turn a visit transcript into tasks and reminders.

The model only PROPOSES. Code then:
  1. drops any task whose quote is not really in the transcript (grounding),
  2. turns date words in the quote into real dates (dates.resolve),
  3. tags medicines "verify with your doctor or midwife",
  4. flags red-flag phrases found anywhere in the transcript.
The user confirms every task before it reaches the calendar.
"""
from __future__ import annotations

import json
import re
from datetime import date

from . import dates, safety
from .grounding import keep_grounded

KINDS = {"test", "appointment", "medicine", "advice", "other"}

SYSTEM_PROMPT = """You read a transcript of a pregnancy check-up conversation. It may be Filipino, English or Taglish and may contain speech-recognition errors.
List what the health worker told the patient to do, and any question the patient asked that was not answered.

Rules:
1. Output ONLY valid JSON, no other text, in this shape:
{"summary": "...", "tasks": [{"title": "...", "kind": "test|appointment|medicine|advice|other", "quote": "..."}], "unanswered_questions": [{"question": "...", "quote": "..."}]}
2. Include only what the transcript says. Never add advice, doses, dates or medicines that are not in it.
3. "quote" must be copied EXACTLY from the transcript, one sentence or less. Do not paraphrase it.
4. "title" is a short phrase in the same language as the quote.
5. For medicines or supplements, copy only what was said. Do not add doses or instructions.
6. "summary" is at most two sentences and only about what the transcript says.
7. If nothing applies, return empty lists."""


class ExtractError(ValueError):
    """The model did not return usable JSON."""


def parse_json_lenient(text: str) -> dict:
    """Pull the first JSON object out of a model reply (models sometimes add fences or chatter)."""
    text = re.sub(r"```(?:json)?", "", text or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ExtractError("The model reply had no JSON object.")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ExtractError(f"The model reply was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtractError("The model reply was not a JSON object.")
    return data


def _clean_tasks(raw) -> list[dict]:
    out = []
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, dict):
            continue
        title, quote = t.get("title"), t.get("quote")
        if not isinstance(title, str) or not isinstance(quote, str) or not title.strip() or not quote.strip():
            continue
        kind = t.get("kind") if t.get("kind") in KINDS else "other"
        out.append({"title": title.strip(), "kind": kind, "quote": quote.strip()})
    return out


def _clean_questions(raw) -> list[dict]:
    out = []
    for q in raw if isinstance(raw, list) else []:
        if isinstance(q, dict) and isinstance(q.get("question"), str) and isinstance(q.get("quote"), str):
            out.append({"question": q["question"].strip(), "quote": q["quote"].strip()})
    return out


def extract_visit(transcript: str, visit_date: date, llm) -> dict:
    """Run the Visit Notes pipeline. Raises ExtractError if the model output is unusable."""
    reply = llm.chat("extract", SYSTEM_PROMPT, transcript, json_mode=True)
    data = parse_json_lenient(reply)

    proposed = _clean_tasks(data.get("tasks"))
    kept, rejected = keep_grounded(proposed, transcript)

    tasks, seen = [], set()
    for i, t in enumerate(kept, start=1):
        key = (t["kind"], t["quote"].casefold())
        if key in seen:
            continue
        seen.add(key)
        r = dates.resolve(t["quote"], visit_date)
        tasks.append({
            "id": f"t{i}",
            "title": t["title"],
            "kind": t["kind"],
            "quote": t["quote"],
            "date": r["date"],
            "time": r["time"],
            "matched": r["matched"],
            "verify": t["kind"] == "medicine",
        })

    questions, _ = keep_grounded(_clean_questions(data.get("unanswered_questions")), transcript)

    summary = data.get("summary") if isinstance(data.get("summary"), str) else ""
    return {
        "summary": summary.strip(),
        "tasks": tasks,
        "unanswered": questions,
        "rejected": len(rejected),
        "emergency": safety.check_emergency(transcript),
        "model": getattr(llm, "name", "unknown"),
        "visit_date": visit_date.isoformat(),
    }
