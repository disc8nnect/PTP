"""Visit Notes: turn a visit transcript into tasks and reminders.

The model only PROPOSES. Code then:
  1. drops any task whose quote is not really in the transcript (grounding), or is a question
     (a patient's question is never an instruction: a 3B model once turned "Can I still drink
     coffee?" into a task titled "Coffee allowed"),
  2. turns date words in the quote into real dates (dates.resolve), and adds any return visit
     with a clear date that the model missed (the next check-up is the one task that must not
     be lost; a real model dropped "Come back on October 22 at 2 pm" while its summary named it),
  3. tags medicines "verify with your doctor or midwife",
  4. hides the summary if it uses words, numbers or negations the transcript does not (models
     have written "reduce the folic acid" for "keep taking your folic acid"),
  5. flags red-flag phrases found anywhere in the transcript.
The user confirms every task before it reaches the calendar.
"""
from __future__ import annotations

import json
import re
from datetime import date

from . import dates, safety
from .grounding import keep_grounded
from .grounding import quote_is_grounded
from .rag import LANGUAGE_NAMES, adds_nothing, language_of

MIN_SUMMARY_SUPPORT = 0.6  # share of the summary's words that must appear in the transcript

KINDS = {"test", "appointment", "medicine", "advice", "other"}

SYSTEM_PROMPT = """You read a transcript of a pregnancy check-up. It may be in Filipino, English or Taglish and may contain speech-recognition errors.
Find EVERY thing the health worker told the patient to do: tests or lab work, return visits, medicines, vitamins or supplements to take or continue, and other instructions. Also find every question the patient asked that was not answered.

Output ONLY valid JSON, no other text, in this shape:
{"summary": "...", "tasks": [{"title": "...", "kind": "test|appointment|medicine|advice|other", "quote": "..."}], "unanswered_questions": [{"question": "...", "quote": "..."}]}

Rules:
1. One task per instruction. A sentence that gives two instructions gives two tasks.
2. "quote" is copied word for word from the transcript: the words where the instruction is given, without the speaker's name. Never paraphrase it.
3. "title" is a short label of 2 to 6 words, in {language}.
4. Never add anything the transcript does not say: no doses, dates, medicines or advice of your own.
5. "summary" is one or two plain sentences in {language}, only about what was said.
6. If nothing applies, return empty lists.

Example transcript:
Doctor: Magpa-urinalysis po kayo bukas. Inumin ang folic acid araw-araw.
Patient: Puwede po ba akong mag-exercise?
Doctor: Bumalik kayo sa Oktubre 20.
Example output:
{"summary": "Pinagawa ang urinalysis at pinapainom ng folic acid araw-araw. Babalik sa Oktubre 20.", "tasks": [{"title": "Magpa-urinalysis", "kind": "test", "quote": "Magpa-urinalysis po kayo bukas"}, {"title": "Folic acid araw-araw", "kind": "medicine", "quote": "Inumin ang folic acid araw-araw"}, {"title": "Bumalik sa check-up", "kind": "appointment", "quote": "Bumalik kayo sa Oktubre 20"}], "unanswered_questions": [{"question": "Puwede po ba akong mag-exercise?", "quote": "Puwede po ba akong mag-exercise?"}]}"""


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


RETURN_WORDS = re.compile(r"\b(balik|bumalik|babalik|magbalik|come back|return|follow[- ]?up|next visit|check-?up)\b",
                          re.IGNORECASE)
RETURN_TITLE = {"en": "Return visit", "tl": "Bumalik sa check-up"}
_SPEAKER = re.compile(r"^[^\W\d_][\w .'-]{0,24}:\s*", re.UNICODE)


def summary_is_supported(summary: str, transcript: str) -> bool:
    """True if the summary mostly reuses the transcript's words and adds no numbers or negations."""
    return adds_nothing(summary, transcript, MIN_SUMMARY_SUPPORT)


def missed_return_visits(transcript: str, tasks: list[dict], visit_date: date) -> list[dict]:
    """Sentences that set a return visit with a date no task already covers."""
    out = []
    for line in transcript.splitlines():
        for sentence in re.split(r"(?<=[.!?])\s+", _SPEAKER.sub("", line.strip())):
            sentence = sentence.strip()
            if not sentence or sentence.endswith("?") or not RETURN_WORDS.search(sentence):
                continue
            r = dates.resolve(sentence, visit_date)
            if not r["date"]:
                continue
            if any(quote_is_grounded(t["quote"], sentence) or quote_is_grounded(sentence, t["quote"]) for t in tasks):
                continue
            out.append({"quote": sentence.rstrip("."), "date": r["date"], "time": r["time"], "matched": r["matched"]})
    return out


def extract_visit(transcript: str, visit_date: date, llm) -> dict:
    """Run the Visit Notes pipeline. Raises ExtractError if the model output is unusable."""
    system = SYSTEM_PROMPT.replace("{language}", LANGUAGE_NAMES[language_of(transcript)])
    reply = llm.chat("extract", system, transcript, json_mode=True)
    data = parse_json_lenient(reply)

    proposed = _clean_tasks(data.get("tasks"))
    kept, rejected = keep_grounded(proposed, transcript)
    questions_as_tasks = [t for t in kept if t["quote"].rstrip(" .\"'”").endswith("?")]
    kept = [t for t in kept if t not in questions_as_tasks]
    rejected += questions_as_tasks

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

    for extra in missed_return_visits(transcript, tasks, visit_date):
        tasks.append({"id": f"t{len(tasks) + 1}", "title": RETURN_TITLE[language_of(transcript)],
                      "kind": "appointment", "verify": False, "by_code": True, **extra})

    questions, _ = keep_grounded(_clean_questions(data.get("unanswered_questions")), transcript)

    summary = data.get("summary").strip() if isinstance(data.get("summary"), str) else ""
    if summary and not summary_is_supported(summary, transcript):
        summary = ""
    return {
        "summary": summary,
        "tasks": tasks,
        "unanswered": questions,
        "rejected": len(rejected),
        "emergency": safety.check_emergency(transcript),
        "model": getattr(llm, "name", "unknown"),
        "visit_date": visit_date.isoformat(),
    }
