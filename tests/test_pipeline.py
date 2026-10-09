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


def model_tasks(out):
    """Tasks that came from the model, leaving out return visits the date check added."""
    return [t for t in out["tasks"] if not t.get("by_code")]


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
        self.assertEqual([t["title"] for t in model_tasks(out)], ["Real"])
        self.assertEqual(out["rejected"], 1)

    def test_fenced_or_chatty_json_is_accepted(self):
        reply = "Sure! ```json\n" + json.dumps({"tasks": [], "unanswered_questions": []}) + "\n```"
        out = extract.extract_visit(TRANSCRIPT, VISIT, ScriptedLLM(reply))
        self.assertEqual(model_tasks(out), [])

    def test_garbage_reply_raises(self):
        with self.assertRaises(extract.ExtractError):
            extract.extract_visit(TRANSCRIPT, VISIT, ScriptedLLM("I cannot help with that."))

    def test_malformed_items_are_ignored(self):
        reply = json.dumps({"tasks": ["text", {"title": "", "quote": "x"}, {"title": "ok", "quote": 5}]})
        out = extract.extract_visit(TRANSCRIPT, VISIT, ScriptedLLM(reply))
        self.assertEqual(model_tasks(out), [])

    def test_patient_question_is_never_a_task(self):
        # Seen with a real 3B model: the patient's question became a task titled "Coffee allowed".
        reply = json.dumps({"tasks": [
            {"title": "Coffee allowed", "kind": "advice", "quote": "Ano pong pagkain ang dapat kong iwasan?"},
            {"title": "Blood test", "kind": "test", "quote": "Magpa-blood test po kayo bago bumalik"}]})
        out = extract.extract_visit(TRANSCRIPT, VISIT, ScriptedLLM(reply))
        self.assertEqual([t["title"] for t in model_tasks(out)], ["Blood test"])
        self.assertEqual(out["rejected"], 1)

    def test_return_visit_the_model_missed_is_added(self):
        # Seen with a real model: the return visit was in its summary but not in its tasks.
        english = "Midwife: Please get an ultrasound.\nPatient: Can I come back on Monday?\nMidwife: Come back on October 22 at 2 pm."
        reply = json.dumps({"tasks": [{"title": "Ultrasound", "kind": "test", "quote": "Please get an ultrasound"}]})
        out = extract.extract_visit(english, VISIT, ScriptedLLM(reply))
        added = [t for t in out["tasks"] if t.get("by_code")]
        self.assertEqual([(t["title"], t["date"], t["time"]) for t in added], [("Return visit", "2026-10-22", "14:00")])
        self.assertEqual(added[0]["quote"], "Come back on October 22 at 2 pm")  # never the patient's question

    def test_return_visit_the_model_found_is_not_added_twice(self):
        out = extract.extract_visit(TRANSCRIPT, VISIT, MockLLM())
        self.assertEqual([t for t in out["tasks"] if t.get("by_code")], [])

    def test_summary_with_words_not_in_the_visit_is_hidden(self):
        # Seen with real models: "reduce the folic acid" for "keep taking your folic acid".
        bad = json.dumps({"summary": "Bawasan ang iron tablet sa 500 mg araw-araw.", "tasks": []})
        self.assertEqual(extract.extract_visit(TRANSCRIPT, VISIT, ScriptedLLM(bad))["summary"], "")
        flipped = json.dumps({"summary": "Huwag ituloy ang iron tablet na nasa reseta.", "tasks": []})
        self.assertEqual(extract.extract_visit(TRANSCRIPT, VISIT, ScriptedLLM(flipped))["summary"], "")
        good = json.dumps({"summary": "Magpa-blood test bago bumalik at ituloy ang iron tablet.", "tasks": []})
        self.assertEqual(extract.extract_visit(TRANSCRIPT, VISIT, ScriptedLLM(good))["summary"],
                         "Magpa-blood test bago bumalik at ituloy ang iron tablet.")

    def test_prompt_names_the_visit_language(self):
        seen = []
        llm = ScriptedLLM("{}")
        llm.chat = lambda task, system, user, json_mode=False: seen.append(system) or "{}"
        extract.extract_visit("Midwife: Please get an ultrasound.", VISIT, llm)
        extract.extract_visit(TRANSCRIPT, VISIT, llm)
        self.assertIn("in English", seen[0])
        self.assertIn("in Filipino (Tagalog)", seen[1])

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

    def test_invented_content_is_replaced_by_the_guide(self):
        # Seen with a real 3B model: foods the guide never mentions.
        r = self.ask("Anong prutas ang maganda?", ScriptedLLM("Kumain ng aprikot at papaya araw-araw [1]."))
        self.assertEqual(r["kind"], "answer")
        self.assertTrue(r["from_guide"])
        self.assertNotIn("aprikot", r["answer"])
        self.assertIn("Kumain ng iba't ibang prutas at gulay araw-araw", r["answer"])

    def test_invented_number_is_replaced_by_the_guide(self):
        r = self.ask("Uminom ba ako ng tubig?", ScriptedLLM("Uminom ng 8 basong malinis na tubig [1]."))
        self.assertTrue(r["from_guide"])
        self.assertNotIn("8", r["answer"])

    def test_answer_in_the_wrong_language_is_replaced(self):
        r = self.ask("Which fruit is good?", ScriptedLLM("Kumain ng iba't ibang prutas at gulay araw-araw [1]."))
        self.assertTrue(r["from_guide"])
        self.assertEqual(r["answer"], "Eat a variety of fruits and vegetables every day. [1]")

    def test_negation_the_guide_does_not_have_is_dropped(self):
        r = self.ask("Anong prutas ang maganda?", ScriptedLLM("Hindi dapat kumain ng prutas araw-araw [1]."))
        self.assertTrue(r["from_guide"])
        self.assertNotIn("Hindi", r["answer"])

    def test_supported_sentences_are_kept_and_the_rest_dropped(self):
        reply = ("Kumain ng iba't ibang prutas araw-araw [1]. Kumain ng iba't ibang prutas araw-araw [1]. "
                 "Kumain ng aprikot at papaya [1].")
        r = self.ask("Anong prutas ang maganda?", ScriptedLLM(reply))
        self.assertFalse(r["from_guide"])
        self.assertEqual(r["answer"], "Kumain ng iba't ibang prutas araw-araw [1].")

    def test_faithful_answer_is_kept(self):
        reply = "Eat a variety of fruits and vegetables every day [1]."
        r = self.ask("Which fruit is good?", ScriptedLLM(reply))
        self.assertFalse(r["from_guide"])
        self.assertEqual(r["answer"], reply)

    def test_prompt_names_the_language(self):
        seen = []
        llm = ScriptedLLM("NOT_FOUND")
        llm.chat = lambda task, system, user, json_mode=False: seen.append(system) or "NOT_FOUND"
        self.ask("Which fruit is good?", llm)
        self.ask("Anong prutas ang maganda?", llm)
        self.assertIn("Write the answer in English.", seen[0])
        self.assertIn("Write the answer in Filipino (Tagalog).", seen[1])

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
