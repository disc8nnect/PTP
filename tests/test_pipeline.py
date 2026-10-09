"""Pipeline tests. They use MockLLM (a rule-based stand-in, not an AI) so they run anywhere,
offline, in milliseconds. They check the CODE around the model: grounding, date resolution,
safety rules, citation checks, storage. They say nothing about how good a real model is."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ptp import extract, rag
from ptp.llm import MockLLM
from ptp.store import Store

DATA = Path(__file__).resolve().parent.parent / "data"
TRANSCRIPT = (DATA / "sample_transcript.txt").read_text(encoding="utf-8")
VISIT = date(2026, 10, 8)  # a Thursday


class ScriptedLLM:
    """Returns a fixed reply, to test how the code treats bad model output."""
    name = "scripted"

    def __init__(self, reply):
        self.reply = reply

    def chat(self, task, system, user, json_mode=False):
        return self.reply


class VisitNotes(unittest.TestCase):
    def test_sample_visit_gives_grounded_tasks_with_dates(self):
        out = extract.extract_visit(TRANSCRIPT, VISIT, MockLLM())
        kinds = {t["kind"] for t in out["tasks"]}
        self.assertTrue({"test", "appointment", "medicine"} <= kinds, out["tasks"])
        appt = next(t for t in out["tasks"] if t["kind"] == "appointment")
        self.assertEqual((appt["date"], appt["time"]), ("2026-10-15", "09:00"))
        medicine = next(t for t in out["tasks"] if t["kind"] == "medicine")
        self.assertTrue(medicine["verify"])
        self.assertEqual(len(out["unanswered"]), 1)

    def test_invented_quote_is_dropped(self):
        reply = json.dumps({"summary": "x", "tasks": [
            {"title": "Real", "kind": "test", "quote": "Magpa-blood test po kayo bago bumalik"},
            {"title": "Invented", "kind": "medicine", "quote": "Uminom ng 500 mg dalawang beses araw-araw"}]})
        out = extract.extract_visit(TRANSCRIPT, VISIT, ScriptedLLM(reply))
        self.assertEqual([t["title"] for t in out["tasks"]], ["Real"])
        self.assertEqual(out["rejected"], 1)

    def test_fenced_or_chatty_json_is_accepted(self):
        reply = "Sure! ```json\n" + json.dumps({"tasks": [], "unanswered_questions": []}) + "\n```"
        out = extract.extract_visit(TRANSCRIPT, VISIT, ScriptedLLM(reply))
        self.assertEqual(out["tasks"], [])

    def test_garbage_reply_raises(self):
        with self.assertRaises(extract.ExtractError):
            extract.extract_visit(TRANSCRIPT, VISIT, ScriptedLLM("I cannot help with that."))

    def test_malformed_items_are_ignored(self):
        reply = json.dumps({"tasks": ["text", {"title": "", "quote": "x"}, {"title": "ok", "quote": 5}]})
        out = extract.extract_visit(TRANSCRIPT, VISIT, ScriptedLLM(reply))
        self.assertEqual(out["tasks"], [])

    def test_emergency_words_in_transcript_are_flagged(self):
        out = extract.extract_visit(TRANSCRIPT + "\nMaria: Dumudugo po ako kagabi.", VISIT, MockLLM())
        self.assertTrue(out["emergency"])


class Ask(unittest.TestCase):
    chunks = rag.load_guides()

    def ask(self, q, llm=None):
        return rag.answer(q, self.chunks, llm or MockLLM())

    def test_guides_load_and_are_marked_sample(self):
        self.assertGreaterEqual(len(self.chunks), 5)
        self.assertTrue(all(c["sample"] for c in self.chunks))

    def test_covered_question_gets_cited_answer(self):
        r = self.ask("Anong prutas ang maganda para sa akin?")
        self.assertEqual(r["kind"], "answer", r)
        self.assertTrue(r["citations"])
        self.assertTrue(r["sample_guides"])

    def test_off_topic_question_is_not_found(self):
        self.assertEqual(self.ask("How do I fix my motorcycle engine?")["kind"], "not_found")

    def test_medicine_and_diagnosis_are_declined_without_calling_the_model(self):
        boom = ScriptedLLM("should not be used [1]")
        boom.chat = lambda *a, **k: (_ for _ in ()).throw(AssertionError("model was called"))
        self.assertEqual(self.ask("Puwede ba akong uminom ng gamot sa sakit ng ulo?", boom)["kind"], "medication")
        self.assertEqual(self.ask("Is this normal?", boom)["kind"], "diagnosis")

    def test_emergency_beats_everything(self):
        self.assertEqual(self.ask("Dumudugo po ako, anong prutas ang kainin ko?")["kind"], "emergency")

    def test_answer_without_valid_citation_fails_closed(self):
        self.assertEqual(self.ask("Anong prutas ang maganda?", ScriptedLLM("Kumain ng saging."))["kind"], "not_found")
        self.assertEqual(self.ask("Anong prutas ang maganda?", ScriptedLLM("Kumain ng saging [9]."))["kind"], "not_found")
        self.assertEqual(self.ask("Anong prutas ang maganda?", ScriptedLLM("NOT_FOUND"))["kind"], "not_found")

    def test_language_follows_the_question(self):
        self.assertEqual(self.ask("Which fruit is good?")["lang"], "en")
        self.assertEqual(self.ask("Anong prutas ang maganda?")["lang"], "tl")
        self.assertIn("doktor", self.ask("Puwede ba akong uminom ng gamot?")["answer"])


class Storage(unittest.TestCase):
    def test_tasks_persist_and_next_appointment(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Store(Path(tmp))
            s.set_profile("Maria", date(2026, 4, 24))
            added = s.add_tasks([
                {"title": "Balik", "kind": "appointment", "quote": "q", "date": "2026-10-15", "time": "09:00"},
                {"title": "Later", "kind": "appointment", "quote": "q", "date": "2026-11-01", "time": None},
                {"title": "Test", "kind": "test", "quote": "q", "date": None, "time": None}], "2026-10-08")
            self.assertEqual(len(Store(Path(tmp)).snapshot()["tasks"]), 3)
            self.assertEqual(s.next_appointment(date(2026, 10, 9))["title"], "Balik")
            s.toggle_task(added[0]["id"])
            self.assertEqual(s.next_appointment(date(2026, 10, 9))["title"], "Later")


if __name__ == "__main__":
    unittest.main()
