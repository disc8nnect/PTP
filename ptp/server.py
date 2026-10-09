"""PTP server: standard library only. Serves the web app and a small JSON API.

Run:   python3 -m ptp.server            (http://127.0.0.1:8765)
       PTP_MOCK=1 python3 -m ptp.server     (test mode with a rule-based stand-in, NOT AI)

Binds to 127.0.0.1 by default so only this machine can reach it. To let a phone on the same
hotspot connect, set PTP_HOST=0.0.0.0 (see README: browsers only allow the microphone on
localhost or HTTPS, so recording works best on the laptop itself).
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import dates, extract, geo, rag, safety, stt
from .llm import LLMUnavailable, ModelMissing, get_llm
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
MAX_BODY = 60 * 1024 * 1024
# Fixed types: Windows can map .js to text/plain through its registry.
TYPES = {".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml"}


def today() -> date:
    override = os.environ.get("PTP_TODAY")
    return date.fromisoformat(override) if override else date.today()


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str = "error"):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


def llm_error(exc: LLMUnavailable) -> ApiError:
    """A model problem as an API error; "not downloaded" gets its own code so the app can say so."""
    if isinstance(exc, ModelMissing):
        return ApiError(503, str(exc), "llm_model_missing")
    return ApiError(503, str(exc), "llm_unavailable")


class App:
    """All the app logic, separate from HTTP so tests and demo_check can call it directly."""

    def __init__(self, store: Store | None = None, llm=None, guides_dir: Path | None = None,
                 facilities_path: Path | None = None, flags_path: Path | None = None):
        self.store = store or Store()
        self.llm = llm or get_llm()
        self.chunks = rag.load_guides(guides_dir)
        self.flags = safety.load_red_flags(flags_path)
        fpath = facilities_path or ROOT / "data" / "facilities.json"
        with open(fpath, encoding="utf-8") as fh:
            self.facility_data = json.load(fh)

    # ---------------------------------------------------------------- helpers
    def _profile(self) -> dict | None:
        p = self.store.snapshot()["profile"]
        if not p.get("lmp"):
            return None
        lmp = date.fromisoformat(p["lmp"])
        t = today()
        weeks, days = dates.gestational_age(lmp, t) if lmp <= t else (0, 0)
        return {
            "name": p.get("name", ""),
            "lmp": lmp.isoformat(),
            "weeks": weeks,
            "days": days,
            "trimester": dates.trimester(weeks),
            "due_date": dates.due_date(lmp).isoformat(),
            "progress": round(dates.progress_fraction(lmp, t), 4),
        }

    # ---------------------------------------------------------------- endpoints
    def health(self) -> dict:
        return {
            "mode": getattr(self.llm, "name", "unknown"),
            "llm_ready": bool(self.llm.available()),
            "llm_model": getattr(self.llm, "model", None),
            "stt": stt.backend(),
            "red_flags_reviewed": bool(self.flags.get("reviewed")),
            "guide_chunks": len(self.chunks),
            "guides_are_sample": any(c["sample"] for c in self.chunks),
            "facilities_are_sample": bool(self.facility_data.get("sample")),
            "today": today().isoformat(),
        }

    def get_profile(self) -> dict:
        return {"profile": self._profile()}

    def set_profile(self, body: dict) -> dict:
        try:
            lmp = date.fromisoformat(str(body.get("lmp", "")))
        except ValueError:
            raise ApiError(400, "lmp must be a date like 2026-04-24", "bad_lmp")
        if lmp > today():
            raise ApiError(400, "The last menstrual period cannot be in the future.", "lmp_future")
        if (today() - lmp).days > 300:
            raise ApiError(400, "That date is more than 300 days ago. Please check it.", "lmp_too_old")
        self.store.set_profile(str(body.get("name", "")).strip()[:60], lmp)
        return self.get_profile()

    def home(self) -> dict:
        snap = self.store.snapshot()
        open_tasks = [t for t in snap["tasks"] if not t["done"]]
        open_tasks.sort(key=lambda t: (t["date"] or "9999", t["time"] or ""))
        return {
            "profile": self._profile(),
            "today": today().isoformat(),
            "next_appointment": self.store.next_appointment(today()),
            "tasks": open_tasks[:6],
            "saved_questions": snap["questions"],
        }

    def calendar(self, year: int, month: int) -> dict:
        if not (1 <= month <= 12 and 1900 < year < 2200):
            raise ApiError(400, "bad month", "bad_month")
        first = date(year, month, 1)
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        count = (nxt - first).days
        by_date: dict[str, list[dict]] = {}
        for t in self.store.snapshot()["tasks"]:
            if t["date"]:
                by_date.setdefault(t["date"], []).append(
                    {"title": t["title"], "kind": t["kind"], "time": t["time"], "done": t["done"]})
        p = self._profile()
        due = p["due_date"] if p else None
        days = []
        for n in range(1, count + 1):
            iso = date(year, month, n).isoformat()
            days.append({"n": n, "date": iso, "today": iso == today().isoformat(), "due": iso == due,
                         "items": by_date.get(iso, [])})
        return {"year": year, "month": month, "lead_blanks": (first.weekday() + 1) % 7, "days": days,
                "profile": p, "agenda": self._agenda()}

    def _agenda(self) -> list[dict]:
        out = [t for t in self.store.snapshot()["tasks"]
               if t["date"] and t["date"] >= today().isoformat() and not t["done"]]
        out.sort(key=lambda t: (t["date"], t["time"] or ""))
        return out[:6]

    def transcribe(self, audio: bytes, ext: str) -> dict:
        if not audio:
            raise ApiError(400, "No audio received.", "no_audio")
        if stt.backend() is None:
            raise ApiError(503, "No local speech model is installed. Paste the transcript instead.", "stt_unavailable")
        ext = re.sub(r"[^a-z0-9]", "", ext.lower())[:5] or "webm"
        path = self.store.folder / "recordings" / f"{int(time.time())}.{ext}"
        path.write_bytes(audio)
        try:
            text = stt.transcribe(path)
        except stt.STTUnavailable as exc:
            raise ApiError(503, str(exc), "stt_unavailable")
        except Exception as exc:  # decoding problems, missing model files, and so on
            raise ApiError(500, f"Transcription failed: {exc}", "stt_failed")
        return {"transcript": text, "recording": path.name}

    def extract(self, body: dict) -> dict:
        transcript = str(body.get("transcript", "")).strip()
        if not transcript:
            raise ApiError(400, "The transcript is empty.", "no_transcript")
        try:
            visit = date.fromisoformat(str(body.get("visit_date") or today().isoformat()))
        except ValueError:
            raise ApiError(400, "visit_date must look like 2026-10-08", "bad_date")
        try:
            return extract.extract_visit(transcript, visit, self.llm)
        except LLMUnavailable as exc:
            raise llm_error(exc)
        except extract.ExtractError as exc:
            raise ApiError(422, str(exc), "bad_model_output")

    def confirm_tasks(self, body: dict) -> dict:
        tasks = body.get("tasks") or []
        questions = [q.strip() for q in body.get("questions") or [] if isinstance(q, str) and q.strip()]
        if not isinstance(tasks, list) or not (tasks or questions):
            raise ApiError(400, "Nothing to save.", "no_tasks")
        added = self.store.add_tasks(tasks, str(body.get("visit_date") or today().isoformat()),
                                     str(body.get("summary") or "")) if tasks else []
        for q in questions:
            self.store.add_question(q)
        return {"tasks": added}

    def toggle_task(self, body: dict) -> dict:
        t = self.store.toggle_task(str(body.get("id", "")))
        if not t:
            raise ApiError(404, "Task not found.", "not_found")
        return {"task": t}

    def ask(self, body: dict) -> dict:
        q = str(body.get("question", "")).strip()
        if not q:
            raise ApiError(400, "Type a question first.", "no_question")
        try:
            return rag.answer(q[:500], self.chunks, self.llm, self.flags)
        except LLMUnavailable as exc:
            raise llm_error(exc)

    def save_question(self, body: dict) -> dict:
        """Save a question for the next check-up. Only when the user taps the button: an
        automatic save once put "How do I fix my car engine?" on the midwife summary."""
        q = str(body.get("question", "")).strip()[:300]
        if not q:
            raise ApiError(400, "Type a question first.", "no_question")
        self.store.add_question(q)
        return {"questions": self.store.snapshot()["questions"]}

    def facilities(self, lat: float | None, lon: float | None, kind: str | None) -> dict:
        default = self.facility_data["default_location"]
        used_default = lat is None or lon is None
        lat, lon = (default["lat"], default["lon"]) if used_default else (lat, lon)
        return {
            "location": {"lat": lat, "lon": lon, "name": default["name"] if used_default else "Your location",
                         "is_default": used_default},
            "sample": bool(self.facility_data.get("sample")),
            "facilities": geo.nearest(self.facility_data["facilities"], lat, lon, kind),
        }

    def sample(self) -> dict:
        """A fictional staged visit, for demos and for trying the app without a recording."""
        text = (ROOT / "data" / "sample_transcript.txt").read_text(encoding="utf-8")
        return {"transcript": text.strip(), "visit_date": "2026-10-08",
                "note": "Fictional, staged conversation. Not a real consultation."}

    def red_flags(self) -> dict:
        return {"reviewed": bool(self.flags.get("reviewed")), "note": self.flags.get("review_note", ""),
                "source": self.flags.get("source", ""),
                "signs": [{"en": s["en"], "tl": s["tl"]} for s in self.flags.get("signs", [])]}

    def summary(self) -> dict:
        snap = self.store.snapshot()
        return {
            "profile": self._profile(),
            "today": today().isoformat(),
            "tasks": [t for t in snap["tasks"] if not t["done"]],
            "questions": snap["questions"],
            "note": "Made by the PTP app from what the user confirmed. It does not replace a doctor or midwife.",
        }


# ----------------------------------------------------------------------------- HTTP


def route(app: App, method: str, path: str, query: dict, body: bytes, headers) -> dict:
    def js() -> dict:
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "Request body must be JSON.", "bad_json")
        if not isinstance(data, dict):
            raise ApiError(400, "Request body must be a JSON object.", "bad_json")
        return data

    def num(name):
        try:
            return float(query[name][0]) if name in query else None
        except ValueError:
            raise ApiError(400, f"{name} must be a number.", "bad_number")

    if method == "GET":
        if path == "/api/health":
            return app.health()
        if path == "/api/profile":
            return app.get_profile()
        if path == "/api/home":
            return app.home()
        if path == "/api/calendar":
            t = today()
            return app.calendar(int(query.get("year", [t.year])[0]), int(query.get("month", [t.month])[0]))
        if path == "/api/facilities":
            return app.facilities(num("lat"), num("lon"), (query.get("kind") or [None])[0])
        if path == "/api/redflags":
            return app.red_flags()
        if path == "/api/summary":
            return app.summary()
        if path == "/api/sample":
            return app.sample()
    elif method == "POST":
        if path == "/api/profile":
            return app.set_profile(js())
        if path == "/api/transcribe":
            return app.transcribe(body, headers.get("X-Audio-Ext", "webm"))
        if path == "/api/extract":
            return app.extract(js())
        if path == "/api/tasks":
            return app.confirm_tasks(js())
        if path == "/api/tasks/toggle":
            return app.toggle_task(js())
        if path == "/api/ask":
            return app.ask(js())
        if path == "/api/questions":
            return app.save_question(js())
    raise ApiError(404, "No such endpoint.", "not_found")


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PTP"

        def log_message(self, fmt, *args):  # quiet; the console is for the demo
            if os.environ.get("PTP_LOG"):
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, status: int, payload: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, status: int, data: dict):
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _handle(self, method: str):
            url = urlparse(self.path)
            if url.path.startswith("/api/"):
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._json(413, {"error": "Request too large.", "code": "too_large"})
                body = self.rfile.read(length) if length else b""
                try:
                    return self._json(200, route(app, method, url.path, parse_qs(url.query), body, self.headers))
                except ApiError as exc:
                    return self._json(exc.status, {"error": exc.message, "code": exc.code})
                except Exception as exc:  # never leak a stack trace to the browser
                    sys.stderr.write(f"Unhandled error on {self.path}: {exc!r}\n")
                    return self._json(500, {"error": "Something went wrong on the device.", "code": "server_error"})
            if method != "GET":
                return self._json(405, {"error": "Method not allowed.", "code": "bad_method"})
            rel = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
            target = (WEB / rel).resolve()
            if WEB.resolve() not in target.parents or not target.is_file():
                return self._send(404, b"Not found", "text/plain; charset=utf-8")
            ctype = TYPES.get(target.suffix.lower()) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

    return Handler


def serve(app: App, host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(app))


def main() -> None:
    host = os.environ.get("PTP_HOST", "127.0.0.1")
    port = int(os.environ.get("PTP_PORT", "8765"))
    app = App()
    h = app.health()
    print(f"PTP running at http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}")
    print(f"  AI mode: {h['mode']}  (model reachable: {h['llm_ready']})  speech-to-text: {h['stt'] or 'none installed'}")
    if h["mode"] == "mock":
        print("  MOCK MODE: a rule-based stand-in is answering, not an AI model. For tests only.")
    if not h["red_flags_reviewed"]:
        print("  Warning: the red-flag list is an unreviewed placeholder.")
    if h["guides_are_sample"]:
        print("  Note: the guides are not yet reviewed by a health worker.")
    if h["llm_ready"] and hasattr(app.llm, "warm_up"):
        print("  Loading the AI model in the background, so the first question is fast.")
        threading.Thread(target=app.llm.warm_up, daemon=True).start()
    try:
        serve(app, host, port).serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
