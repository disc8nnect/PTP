import unittest

from ptp import geo, safety
from ptp.grounding import keep_grounded, quote_is_grounded


class EmergencyAndQuestionRules(unittest.TestCase):
    def kind(self, text):
        return safety.classify_question(text)["kind"]

    def test_emergency_phrases_in_both_languages(self):
        self.assertTrue(safety.check_emergency("Dumudugo po ako ngayon"))
        self.assertTrue(safety.check_emergency("I have severe headache and blurred vision"))
        self.assertTrue(safety.check_emergency("Hindi na gumagalaw ang baby ko"))
        self.assertTrue(safety.check_emergency("I can't breathe"))

    def test_plain_question_is_ok(self):
        self.assertEqual(self.kind("Anong prutas ang maganda para sa akin?"), "ok")

    def test_medication_is_declined(self):
        self.assertEqual(self.kind("Puwede ba akong uminom ng gamot sa sakit ng ulo?"), "medication")
        self.assertEqual(self.kind("What dose of paracetamol can I take?"), "medication")

    def test_diagnosis_is_declined(self):
        self.assertEqual(self.kind("Normal ba ang sakit ng likod ko?"), "diagnosis")
        self.assertEqual(self.kind("Is this normal?"), "diagnosis")

    def test_emergency_beats_medication(self):
        self.assertEqual(self.kind("Matinding sakit ng ulo, anong gamot ang iinumin ko?"), "emergency")

    def test_red_flag_file_is_marked_unreviewed(self):
        flags = safety.load_red_flags()
        self.assertFalse(flags["reviewed"], "Flip this test only after a health worker reviews the list.")


class Grounding(unittest.TestCase):
    transcript = "Magpa-blood test po kayo bago bumalik. Balik kayo sa susunod na Huwebes."

    def test_quote_must_be_in_source(self):
        self.assertTrue(quote_is_grounded("magpa blood test po kayo", self.transcript))
        self.assertFalse(quote_is_grounded("Uminom ng bitamina araw-araw", self.transcript))
        self.assertFalse(quote_is_grounded("", self.transcript))

    def test_keep_grounded_splits(self):
        kept, rejected = keep_grounded(
            [{"quote": "Balik kayo sa susunod na Huwebes"}, {"quote": "made up sentence"}], self.transcript)
        self.assertEqual((len(kept), len(rejected)), (1, 1))


class Distance(unittest.TestCase):
    def test_one_degree_of_longitude_at_equator(self):
        self.assertAlmostEqual(geo.haversine_km(0, 0, 0, 1), 111.19, delta=0.1)

    def test_nearest_sorted_and_filtered(self):
        facilities = [
            {"name": "far", "kind": "hospital", "lat": 14.90, "lon": 120.90},
            {"name": "near", "kind": "rhu", "lat": 14.851, "lon": 120.811},
            {"name": "mid", "kind": "midwife", "lat": 14.86, "lon": 120.82},
        ]
        out = geo.nearest(facilities, 14.85, 120.81)
        self.assertEqual([f["name"] for f in out], ["near", "mid", "far"])
        only = geo.nearest(facilities, 14.85, 120.81, kind="hospital")
        self.assertEqual([f["name"] for f in only], ["far"])


if __name__ == "__main__":
    unittest.main()
