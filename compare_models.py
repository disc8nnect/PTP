"""Try local models on YOUR laptop and compare them. Run with Ollama running and each model pulled:

    python3 compare_models.py llama3.2:3b qwen2.5:3b gemma3:4b

For each model it runs the sample visit and a few Ask questions, then prints time and counts.
It does NOT measure accuracy for you: read the printed tasks and answers yourself and decide.
"""
import os
import sys
import time
from datetime import date
from pathlib import Path

os.environ.pop("PTP_MOCK", None)
from ptp import extract, rag  # noqa: E402
from ptp.llm import LLMUnavailable, OllamaLLM  # noqa: E402

models = sys.argv[1:]
if not models:
    sys.exit(__doc__)
transcript = (Path(__file__).parent / "data" / "sample_transcript.txt").read_text(encoding="utf-8")
chunks = rag.load_guides()
QUESTIONS = [
    ("What fruit is good for me?", "answer"),
    ("Anong pagkain ang dapat kong iwasan?", "answer or not_found - read it"),
    ("How do I fix my car engine?", "not_found"),
    ("Who won the basketball game last night?", "not_found"),
]

for model in models:
    llm = OllamaLLM(model=model)
    print(f"\n===== {model} =====")
    if not llm.available():
        print("  Ollama not reachable. Start it and run:  ollama pull", model)
        continue
    try:
        t0 = time.time()
        out = extract.extract_visit(transcript, date(2026, 10, 8), llm)
        print(f"  Visit notes: {time.time() - t0:.1f}s, {len(out['tasks'])} tasks kept, {len(out['rejected'])} rejected as not in transcript")
        for t in out["tasks"]:
            print(f"    - [{t['kind']}] {t['title']}  date={t['date']} time={t['time']}")
        print("  Summary:", out["summary"])
    except (LLMUnavailable, extract.ExtractError) as exc:
        print("  Visit notes FAILED:", exc)
    for q, expect in QUESTIONS:
        try:
            t0 = time.time()
            r = rag.answer(q, chunks, llm)
            print(f"  Ask ({time.time() - t0:.1f}s) expected {expect}: {q!r} -> {r['kind']}: {r['answer'][:90]}")
        except LLMUnavailable as exc:
            print("  Ask FAILED:", exc)
