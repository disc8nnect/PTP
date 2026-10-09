"""The guides shipped in data/guides: their format, and that everyday questions find them.

Ask only answers from these files, so a question that finds no paragraph gets "not found".
Each paragraph is Tagalog first, then English, so the app can quote it in either language."""
import re
import unittest

from ptp import rag, safety

CHUNKS = rag.load_guides()

# Everyday questions (English, Tagalog, Taglish) and words the paragraph that answers them contains.
# Some are questions the real model could not answer before the guide covered them.
QUESTIONS = [
    ("What fruit is good for me?", "fruit"),
    ("Anong prutas ang maganda para sa akin?", "prutas"),
    ("Can I drink coffee?", "caffeine"),
    ("Pwede ba akong uminom ng kape?", "caffeine"),
    ("Namamanas ang paa ko, ano ang gagawin?", "swelling"),
    ("How do I stop morning sickness?", "morning sickness"),
    ("Lagi akong nagsusuka, ano ang gagawin ko?", "pagsusuka"),
    ("What should I bring to my check-up?", "Bring your"),
    ("Ilang beses ako dapat magpa-check-up?", "eight"),
    ("Can I eat liver?", "liver"),
    ("Pwede ba ang alak?", "alcohol"),
    ("How should I sleep?", "on your side"),
    ("When will I feel the baby kick?", "16 and 24"),
    ("Ano ang mga senyales ng panganganak?", "Signs of labor"),
    ("How long should I breastfeed?", "breast milk"),
    ("Can I exercise while pregnant?", "150 minutes"),
    ("Masakit ang likod ko", "back pain"),
    ("Can we have sex?", "Sex is safe"),
    ("I feel sad all the time", "tearful"),
    ("Can I eat tuna?", "tuna"),
    ("How much weight should I gain?", "weight before pregnancy"),
    ("Ilang kilo ang dapat kong itaba?", "weight before pregnancy"),
    ("What foods should I avoid?", "Foods to avoid"),
    ("Ano ang dapat kong iwasang kainin?", "Foods to avoid"),
    ("Can I eat sushi?", "frozen first"),
    ("Pwede ba ang kinilaw?", "frozen first"),
]


class GuideFormat(unittest.TestCase):
    def test_there_is_a_real_guide(self):
        self.assertGreaterEqual(len(CHUNKS), 40)

    def test_every_paragraph_is_tagalog_then_english(self):
        for c in CHUNKS:
            langs = [rag.language_of(s) for s in rag._sentences(c["text"])]
            self.assertIn("tl", langs, c["text"][:60])
            self.assertIn("en", langs, c["text"][:60])
            self.assertEqual(langs, sorted(langs, key=lambda l: l != "tl"), c["text"][:60])

    def test_every_paragraph_has_search_words_and_shows_none_of_them(self):
        for c in CHUNKS:
            self.assertTrue(c["keywords"], c["text"][:60])
            self.assertNotIn("keywords:", c["text"])
            self.assertNotIn("<!--", c["text"])

    def test_sources_are_recorded(self):
        for c in CHUNKS:
            for field in ("source", "publisher"):
                self.assertTrue(c[field], f"{c['doc']} has no {field}")

    def test_no_medicine_doses(self):
        # The app never advises on doses; the only amount in mg is the caffeine limit.
        for c in CHUNKS:
            self.assertIsNone(re.search(r"\d\s*(mcg|µg|ug|iu)\b", c["text"], re.IGNORECASE), c["text"][:60])
            if re.search(r"\d\s*mg\b", c["text"]):
                self.assertIn("caffeine", c["text"])


class EverydayQuestions(unittest.TestCase):
    def test_everyday_questions_find_the_right_paragraph(self):
        for question, expected in QUESTIONS:
            with self.subTest(question=question):
                self.assertEqual(safety.classify_question(question)["kind"], "ok", "safety rules took it")
                hits = rag.retrieve(question, CHUNKS)  # the model reads all of these, up to 3
                self.assertTrue(hits, "no paragraph found")
                self.assertTrue(any(expected.casefold() in h["text"].casefold() for h in hits),
                                [h["text"][:50] for h in hits])

    def test_off_topic_questions_find_nothing(self):
        for question in ("How do I fix my car engine?", "Who won the basketball game last night?"):
            self.assertEqual(rag.retrieve(question, CHUNKS), [], question)


if __name__ == "__main__":
    unittest.main()
