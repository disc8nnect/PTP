"""End-to-end tests: a real HTTP server on an ephemeral port, a temp Store, MockLLM (not an AI)."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ["PTP_TODAY"] = "2026-10-09"

from ptp import server
from ptp.llm import MockLLM, ModelMissing, OllamaLLM
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
        # Saving only a question (no to-dos ticked) works; saving nothing is refused.
        s, only_q = self.j("POST", "/api/tasks", {"tasks": [], "questions": ["Puwede ba akong mag-kape?"]})
        self.assertEqual((s, only_q["tasks"]), (200, []))
        self.assertIn("Puwede ba akong mag-kape?", self.j("GET", "/api/summary")[1]["questions"])
        self.assertEqual(self.j("POST", "/api/tasks", {"tasks": [], "questions": []})[1]["code"], "no_tasks")
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
        # Unanswered questions are only offered for the check-up list; the user's tap saves them.
        r = self.j("POST", "/api/ask", {"question": "How do I fix my bicycle?"})[1]
        self.assertTrue(r["add_to_questions"])
        self.assertNotIn("How do I fix my bicycle?", self.j("GET", "/api/summary")[1]["questions"])
        self.assertEqual(self.j("POST", "/api/questions", {"question": "Puwede ba akong mag-swimming?"})[0], 200)
        self.assertIn("Puwede ba akong mag-swimming?", self.j("GET", "/api/summary")[1]["questions"])
        self.assertEqual(self.j("POST", "/api/questions", {"question": " "})[0], 400)
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



class WarmUp(unittest.TestCase):
    """At start, PTP asks Ollama to load the model and keep it loaded, so the first question is fast."""

    def test_warm_up_loads_and_keeps_the_model(self):
        seen = []

        class Ollama(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        fake = HTTPServer(("127.0.0.1", 0), Ollama)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        try:
            llm = OllamaLLM(base_url=f"http://127.0.0.1:{fake.server_address[1]}/v1", model="gemma3:4b")
            self.assertTrue(llm.warm_up())
            self.assertEqual(seen, [("/api/generate", {"model": "gemma3:4b", "keep_alive": "8h"})])
        finally:
            fake.shutdown()
            fake.server_close()
        self.assertFalse(OllamaLLM(base_url="http://127.0.0.1:9/v1").warm_up())  # nothing there: no crash


class ModelNotDownloaded(unittest.TestCase):
    """Ollama is running but the model was never pulled: the app must say that, not "can't connect"."""

    def test_missing_model_gets_its_own_code(self):
        class NotFound(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(404)
                self.end_headers()

            def log_message(self, *args):
                pass

        fake = HTTPServer(("127.0.0.1", 0), NotFound)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        try:
            llm = OllamaLLM(base_url=f"http://127.0.0.1:{fake.server_address[1]}/v1", model="tiny:1b")
            with self.assertRaises(ModelMissing):
                llm.chat("extract", "s", "u")
            with tempfile.TemporaryDirectory() as tmp:
                app = server.App(store=Store(Path(tmp)), llm=llm)
                with self.assertRaises(server.ApiError) as caught:
                    app.ask({"question": "What fruit is good for me?"})
                self.assertEqual(caught.exception.code, "llm_model_missing")
                self.assertEqual(app.health()["llm_model"], "tiny:1b")
        finally:
            fake.shutdown()
            fake.server_close()


if __name__ == "__main__":
    unittest.main()
