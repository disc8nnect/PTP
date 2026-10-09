"""Score local models on PTP's real tasks, on YOUR laptop. Run with Ollama running and each model pulled:

    python3 compare_models.py gemma3:4b llama3.2:3b

For each model it runs three staged visits (Tagalog, English, Taglish) and eleven Ask questions,
checks the results against what a careful reader would expect, and prints a score line plus
everything the model wrote, so you can read it yourself. The visits and questions are our own
small test set, not a benchmark: use it to compare models on this app, not to quote accuracy.
"""
import os
import sys
import time
from datetime import date
from pathlib import Path

os.environ.pop("PTP_MOCK", None)
from ptp import extract, rag  # noqa: E402
from ptp.llm import LLMUnavailable, OllamaLLM  # noqa: E402

VISIT_DATE = date(2026, 10, 8)  # a Thursday
VISITS = [  # name, transcript, kinds of task expected, (date, time) of the return visit, unanswered questions
    ("Tagalog (sample)", (Path(__file__).parent / "data" / "sample_transcript.txt").read_text(encoding="utf-8"),
     {"test", "appointment", "medicine"}, ("2026-10-15", "09:00"), 1),
    ("English", "Midwife: Good morning. Your blood pressure is normal today.\n"
     "Midwife: Please get an ultrasound before your next visit.\n"
     "Midwife: Keep taking your folic acid every morning.\n"
     "Patient: Can I still drink coffee?\n"
     "Midwife: Come back on October 22 at 2 pm.",
     {"test", "appointment", "medicine"}, ("2026-10-22", "14:00"), 1),
    ("Taglish", "Doktora: Okay naman ang baby, malakas ang heartbeat.\n"
     "Doktora: Magpa-urine test ka sa lab ngayong linggo, tapos dalhin mo ang result.\n"
     "Doktora: Take mo pa rin yung calcium, isa sa umaga.\n"
     "Pasyente: Doc, normal lang po ba na sumasakit ang likod ko?\n"
     "Doktora: Normal yan sa second trimester. Balik ka after two weeks.",
     {"test", "appointment", "medicine"}, ("2026-10-22", None), 0),
]
QUESTIONS = [  # question, expected kind
    ("What should I bring to my check-up?", "answer"),
    ("Should I drink water?", "answer"),
    ("Do I need to wash vegetables?", "answer"),
    ("What fruit is good for me?", "answer"),
    ("Anong prutas ang maganda para sa akin?", "answer"),
    ("Ano ang dapat kong dalhin sa check-up?", "answer"),
    ("Kailan ako dapat magpa-check-up?", "answer"),
    ("Ano ang dapat kong kainin araw-araw?", "answer"),
    ("How do I fix my car engine?", "not_found"),
    ("Who won the basketball game last night?", "not_found"),
    ("Can I eat sushi?", "not_found"),
]


class Recorder:
    """Keeps the model's last raw reply so it can be printed next to what the app showed."""

    def __init__(self, llm):
        self.llm, self.name, self.last = llm, llm.name, ""

    def available(self):
        return self.llm.available()

    def chat(self, *args, **kwargs):
        self.last = self.llm.chat(*args, **kwargs)
        return self.last


models = sys.argv[1:]
if not models:
    sys.exit(__doc__)
chunks = rag.load_guides()
results = []

for model in models:
    llm = Recorder(OllamaLLM(model=model))
    print(f"\n===== {model} =====")
    if not llm.available():
        print("  Ollama not reachable. Start it and run:  ollama pull", model)
        continue
    s = {"model": model, "found": 0, "expected": 0, "invented": 0, "dates": 0, "ask": 0, "secs": 0.0}
    try:
        for name, text, kinds, (want_date, want_time), n_questions in VISITS:
            t0 = time.time()
            out = extract.extract_visit(text, VISIT_DATE, llm)
            s["secs"] += time.time() - t0
            from_model = [t for t in out["tasks"] if not t.get("by_code")]
            appt = next((t for t in out["tasks"] if t["kind"] == "appointment"), None)
            s["found"] += len(kinds & {t["kind"] for t in from_model})
            s["expected"] += len(kinds)
            s["invented"] += max(0, len(from_model) - len(kinds)) + out["rejected"]
            s["dates"] += bool(appt) and appt["date"] == want_date and (want_time is None or appt["time"] == want_time)
            print(f"  Visit, {name} ({time.time() - t0:.0f}s): {len(from_model)} to-dos from the model, "
                  f"{len(out['tasks']) - len(from_model)} added by the date check, {out['rejected']} rejected; "
                  f"{len(out['unanswered'])} unanswered question(s), expected {n_questions}")
            print(f"    summary: {out['summary'] or '(hidden: not backed by the transcript)'}")
            for t in out["tasks"]:
                print(f"    - [{t['kind']}] {t['title']}  date={t['date']} time={t['time']}"
                      f"{'  (date check)' if t.get('by_code') else ''}")
        for question, want in QUESTIONS:
            t0, llm.last = time.time(), ""
            r = rag.answer(question, chunks, llm)
            s["secs"] += time.time() - t0
            s["ask"] += r["kind"] == want
            note = " (quoted from the guide)" if r.get("from_guide") else ""
            print(f"  Ask ({time.time() - t0:.0f}s) {'ok ' if r['kind'] == want else 'BAD'} {question!r} -> {r['kind']}{note}")
            print(f"    shown: {r['answer']}")
            if r.get("from_guide") and llm.last:
                print(f"    model wrote: {llm.last}")
    except (LLMUnavailable, extract.ExtractError) as exc:
        print("  FAILED:", exc)
        continue
    results.append(s)

if results:
    print("\nModel                 to-dos found  invented  return dates  Ask right  total time")
    for s in results:
        print(f"{s['model']:<22}{s['found']:>5}/{s['expected']:<8}{s['invented']:>6}{s['dates']:>10}/{len(VISITS)}"
              f"{s['ask']:>10}/{len(QUESTIONS)}{s['secs']:>10.0f}s")
    print("Our own small test set, not a benchmark. Read the output above before choosing.")
