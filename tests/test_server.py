"""End-to-end tests: a real HTTP server on an ephemeral port, a temp Store, MockLLM (not an AI)."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

os.environ["PTP_TODAY"] = "2026-10-09"

from ptp import server
from ptp.llm import MockLLM
from ptp.store import Store


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        app = server.App(store=Store(Path(cls.tmp.name)), llm=MockLLM())
        cls.httpd = server.serve(app, "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, data=None, raw=None, headers=None):
        body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def j(self, method, path, data=None):
        status, body, _ = self.call(method, path, data)
        return status, json.loads(body)

    def test_a_static_files(self):
        for p, ctype in (("/", "text/html"), ("/app.js", "javascript"), ("/style.css", "text/css"),
                         ("/strings.json", "application/json")):
            status, body, h = self.call("GET", p)
            self.assertEqual(status, 200, p)
            self.assertIn(ctype, h["Content-Type"])
        self.assertEqual(self.call("GET", "/../ptp/server.py")[0], 404)
        self.assertEqual(self.call("GET", "/%2e%2e/ptp/server.py")[0], 404)

    def test_b_health_and_flow(self):
        s, h = self.j("GET", "/api/health")
        self.assertEqual((s, h["mode"], h["red_flags_reviewed"]), (200, "mock", False))
        self.assertIsNone(self.j("GET", "/api/profile")[1]["profile"])
        # Each date problem has its own code, so the app can explain it in the chosen language.
        self.assertEqual(self.j("POST", "/api/profile", {"lmp": "2027-01-01"}), (400, {"error": "The last menstrual period cannot be in the future.", "code": "lmp_future"}))
        self.assertEqual(self.j("POST", "/api/profile", {"lmp": "2025-01-01"})[1]["code"], "lmp_too_old")
        self.assertEqual(self.j("POST", "/api/profile", {"lmp": "garbage"})[1]["code"], "bad_lmp")
        s, p = self.j("POST", "/api/profile", {"name": "Maria", "lmp": "2026-04-24"})
        self.assertEqual(p["profile"]["due_date"], "2027-01-29")
        self.assertEqual(p["profile"]["weeks"], 24)

        s, sample = self.j("GET", "/api/sample")
        s, ex = self.j("POST", "/api/extract", {"transcript": sample["transcript"], "visit_date": sample["visit_date"]})
        self.assertEqual(s, 200)
        self.assertGreaterEqual(len(ex["tasks"]), 3)
        s, saved = self.j("POST", "/api/tasks", {"tasks": ex["tasks"], "visit_date": sample["visit_date"],
                                                  "summary": ex["summary"], "questions": ex["unanswered"]})
        self.assertEqual(s, 200)
        tid = saved["tasks"][0]["id"]
        self.assertTrue(self.j("POST", "/api/tasks/toggle", {"id": tid})[1]["task"]["done"])
        self.assertEqual(self.j("POST", "/api/tasks/toggle", {"id": "nope"})[0], 404)

        s, home = self.j("GET", "/api/home")
        self.assertIsNotNone(home["next_appointment"])
        s, cal = self.j("GET", "/api/calendar?year=2026&month=10")
        self.assertEqual(len(cal["days"]), 31)
        self.assertTrue(any(d["items"] for d in cal["days"]))
        s, cal = self.j("GET", "/api/calendar?year=2027&month=1")
        self.assertTrue(next(d for d in cal["days"] if d["n"] == 29)["due"])
        self.assertEqual(self.j("GET", "/api/calendar?year=2026&month=13")[0], 400)
        self.assertEqual(self.j("GET", "/api/summary")[0], 200)

    def test_c_ask(self):
        self.assertEqual(self.j("POST", "/api/ask", {"question": "   "})[0], 400)
        self.assertEqual(self.j("POST", "/api/ask", {"question": "Anong gamot sa sakit ng ulo?"})[1]["kind"], "medication")
        self.assertEqual(self.j("POST", "/api/ask", {"question": "I have heavy bleeding"})[1]["kind"], "emergency")
        self.assertEqual(self.j("POST", "/api/ask", {"question": "How do I fix my car engine?"})[1]["kind"], "not_found")

    def test_d_facilities_and_errors(self):
        s, f = self.j("GET", "/api/facilities")
        self.assertTrue(f["location"]["is_default"])
        self.assertFalse(self.j("GET", "/api/facilities?lat=14.86&lon=120.82")[1]["location"]["is_default"])
        d = [x["distance_km"] for x in f["facilities"]]
        self.assertEqual(d, sorted(d))
        self.assertEqual(self.j("GET", "/api/facilities?lat=abc")[0], 400)
        self.assertEqual(self.j("GET", "/api/nothing")[0], 404)
        self.assertEqual(self.call("POST", "/api/extract", raw=b"not json")[0], 400)
        self.assertEqual(self.call("POST", "/api/transcribe", raw=b"")[0], 400)
        self.assertEqual(self.j("POST", "/api/extract", {"transcript": ""})[0], 400)
        self.assertTrue(len(self.j("GET", "/api/redflags")[1]["signs"]) >= 5)


if __name__ == "__main__":
    unittest.main()
