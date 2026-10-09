"""Screen text (web/strings.json): every line must exist in English and Tagalog, with the same
{placeholders}, and every key the web app uses must be there. A missing key would show up on
screen as the raw key, so this catches it before a user does."""
import json
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
STRINGS = json.loads((WEB / "strings.json").read_text(encoding="utf-8"))
SERVER = (Path(__file__).resolve().parent.parent / "ptp" / "server.py").read_text(encoding="utf-8")


def base_keys(d):
    """Keys without the English-only singular forms ("+1 day"): Tagalog does not need them."""
    return {k for k in d if not k.endswith("_one")}


def placeholders(text):
    return set(re.findall(r"\{(\w+)\}", text))


class Strings(unittest.TestCase):
    en, tl = STRINGS["en"], STRINGS["tl"]

    def test_only_english_and_tagalog(self):
        self.assertEqual(set(STRINGS), {"en", "tl"})

    def test_both_languages_have_the_same_lines(self):
        self.assertEqual(sorted(base_keys(self.en) - base_keys(self.tl)), [], "missing in Tagalog")
        self.assertEqual(sorted(base_keys(self.tl) - base_keys(self.en)), [], "missing in English")
        for d in (self.en, self.tl):
            for k in d:
                if k.endswith("_one"):
                    self.assertIn(k[:-4], d, f"{k} has no plural form")

    def test_placeholders_match(self):
        for d in (self.en, self.tl):
            for k, v in d.items():
                if isinstance(v, str):
                    base = k[:-4] if k.endswith("_one") else k
                    self.assertEqual(placeholders(v), placeholders(self.en[base]), f"{k}: {v!r}")

    def test_lists_are_complete(self):
        for d in (self.en, self.tl):
            self.assertEqual(len(d["months"]), 12)
            self.assertEqual(len(d["monthsShort"]), 12)
            self.assertEqual(len(d["weekdays"]), 7)
            self.assertEqual(len(d["weekdaysShort"]), 7)
            self.assertEqual(set(d["kinds"]), {"rhu", "midwife", "hospital"})

    def test_every_key_the_app_uses_exists(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        html = (WEB / "index.html").read_text(encoding="utf-8")
        used = (set(re.findall(r"\bt\('([\w.]+)'", app)) | set(re.findall(r"\bS\(\)\.(\w+)", app))
                | set(re.findall(r'data-t="([\w.]+)"', html)))
        self.assertGreater(len(used), 100, "the key search found too little; did the code style change?")
        for name, d in STRINGS.items():
            self.assertEqual(sorted(used - set(d)), [], f"used in the app but missing in {name}")

    def test_user_errors_from_the_server_have_text(self):
        # Codes the user can cause; the rest (bad_json, server_error, ...) fall back to error.generic.
        codes = set(re.findall(r'ApiError\(\d+, .+?, "(\w+)"\)', SERVER))
        internal = {"bad_json", "bad_number", "bad_month", "server_error", "bad_method", "too_large"}
        self.assertGreater(len(codes), 10)
        for code in codes - internal:
            self.assertIn(f"error.{code}", self.en, code)


if __name__ == "__main__":
    unittest.main()
