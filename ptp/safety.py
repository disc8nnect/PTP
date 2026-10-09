"""Safety rules. Plain code, no AI: these decide BEFORE any model is asked.

check_emergency(text)   -> phrases from data/red_flags.json found in the text
classify_question(text) -> "emergency" | "medication" | "diagnosis" | "ok"

The red-flag list ships as an UNREVIEWED placeholder (see data/red_flags.json). It must be
replaced with an official list and reviewed by a health worker before any real use.
"""
from __future__ import annotations

import json
from pathlib import Path

from .grounding import _norm

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Words that mean the question is about a medicine or dose. The app never answers these.
MEDICATION_TERMS = [
    "gamot", "medicine", "medication", "drug", "dose", "dosage", "tablet", "tableta", "capsule",
    "kapsula", "antibiotic", "paracetamol", "ibuprofen", "aspirin", "painkiller", "pain reliever",
    "ilang tableta", "ilang patak", "prescription", "reseta", "magkano ang iinumin",
]

# Phrases that ask the app to judge a symptom or a condition. The app never answers these.
DIAGNOSIS_TERMS = [
    "normal ba", "delikado ba", "dapat ba akong mag alala", "may sakit ba ako", "ano ang sakit ko",
    "is it normal", "is this normal", "is it dangerous", "is this dangerous", "do i have",
    "am i sick", "diagnose", "what is wrong with me", "ano ang mali sa akin",
]


def load_red_flags(path: Path | None = None) -> dict:
    p = Path(path) if path else DATA_DIR / "red_flags.json"
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _contains(haystack_norm: str, phrase: str) -> bool:
    p = _norm(phrase)
    return bool(p) and f" {p} " in f" {haystack_norm} "


def check_emergency(text: str, flags: dict | None = None) -> list[str]:
    """Matched red-flag phrases, in the order the signs are listed. Empty list means none found."""
    flags = flags or load_red_flags()
    hay = _norm(text or "")
    matched: list[str] = []
    for sign in flags.get("signs", []):
        for phrase in sign.get("phrases", []):
            if _contains(hay, phrase):
                matched.append(phrase)
                break
    return matched


def classify_question(text: str, flags: dict | None = None) -> dict:
    """Decide how to treat a question. Emergency wins over everything else."""
    emergency = check_emergency(text, flags)
    if emergency:
        return {"kind": "emergency", "matched": emergency}
    hay = _norm(text or "")
    meds = [t for t in MEDICATION_TERMS if _contains(hay, t)]
    if meds:
        return {"kind": "medication", "matched": meds}
    diag = [t for t in DIAGNOSIS_TERMS if _contains(hay, t)]
    if diag:
        return {"kind": "diagnosis", "matched": diag}
    return {"kind": "ok", "matched": []}
