"""One-command offline check. Run it before the demo, with Wi-Fi OFF:

    python3 demo_check.py            # uses your real local model (Ollama) - what the demo needs
    python3 demo_check.py --mock     # uses the rule-based stand-in (NOT AI); proves the app code only
    docker compose exec app python demo_check.py     # the same check inside Docker

It blocks every connection to the internet while it runs (only this machine and private networks,
such as Docker's link to Ollama, are allowed), so a PASS also shows nothing needed the internet. It prints PASS/FAIL per step and exits 1 if any step failed.
"""
import ipaddress
import json
import os
import socket
import sys
import tempfile
import threading
import urllib.request
from datetime import date
from pathlib import Path

MOCK = "--mock" in sys.argv
if MOCK:
    os.environ["PTP_MOCK"] = "1"
os.environ.setdefault("PTP_TODAY", "2026-10-09")

# ---- block anything on the internet ----------------------------------------------------------
_real_connect = socket.socket.connect


def _is_local(host: str) -> bool:
    """This machine, or a private network such as Docker's (where Ollama runs in compose)."""
    if host == "localhost" or host.startswith("/"):
        return True
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False  # a name that was not resolved to an address: refuse
    return ip.is_loopback or ip.is_private


def _local_only(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, str) and not _is_local(host):
        raise OSError(f"BLOCKED non-local connection to {host}")
    return _real_connect(self, address)


socket.socket.connect = _local_only

from ptp import dates, extract, grounding, server, stt  # noqa: E402
from ptp.store import Store  # noqa: E402

results = []


def step(name):
    def deco(fn):
        try:
            detail = fn()
            results.append((True, name, detail or ""))
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            results.append((False, name, f"{type(exc).__name__}: {exc}"))
        return fn
    return deco


tmp = tempfile.TemporaryDirectory()
app = server.App(store=Store(Path(tmp.name)))


@step("Network guard works (outside connection is refused)")
def _():
    try:
        socket.create_connection(("93.184.216.34", 80), timeout=2)
    except OSError as exc:
        assert "BLOCKED" in str(exc), exc
        return
    raise AssertionError("an outside connection was allowed")


@step("Due date from LMP 2026-04-24 is 2027-01-29, week 24, trimester 2")
def _():
    lmp = date(2026, 4, 24)
    assert dates.due_date(lmp) == date(2027, 1, 29)
    w, _d = dates.gestational_age(lmp, date(2026, 10, 9))
    assert (w, dates.trimester(w)) == (24, 2), (w,)


@step(f"AI model reachable ({'mock stand-in' if MOCK else 'local model'})")
def _():
    assert app.llm.available(), "Local model not reachable. Start Ollama and pull the model (see README), or use --mock."
    return getattr(app.llm, "name", "")


transcript = (Path(__file__).parent / "data" / "sample_transcript.txt").read_text(encoding="utf-8")
extracted = {}


@step("Visit notes: >=3 tasks, each quote found in the transcript, dates resolved")
def _():
    out = extract.extract_visit(transcript, date(2026, 10, 8), app.llm)
    extracted.update(out)
    assert len(out["tasks"]) >= 3, f"only {len(out['tasks'])} tasks"
    for t in out["tasks"]:
        assert grounding.quote_is_grounded(t["quote"], transcript), t
    assert any(t["date"] for t in out["tasks"]), "no task got a date"
    return f"{len(out['tasks'])} tasks, {sum(1 for t in out['tasks'] if t['date'])} dated"


@step("Medicine tasks are flagged 'verify with doctor/midwife'")
def _():
    meds = [t for t in extracted.get("tasks", []) if t["kind"] == "medicine"]
    assert meds and all(t["verify"] for t in meds), meds


@step("Saving tasks puts them in the calendar and 'next appointment'")
def _():
    app.set_profile({"name": "Demo", "lmp": "2026-04-24"})
    app.confirm_tasks({"tasks": extracted["tasks"], "visit_date": "2026-10-08", "summary": extracted.get("summary", "")})
    assert app.home()["next_appointment"], "no next appointment"
    cal = app.calendar(2026, 10)
    assert any(d["items"] for d in cal["days"])


@step("Ask: question covered by a guide gets a cited answer")
def _():
    r = app.ask({"question": "What should I bring to my check-up?"})
    assert r["kind"] == "answer" and r["citations"], r
    return r["answer"][:70]


@step("Ask: off-topic question -> 'not found' (does not invent)")
def _():
    assert app.ask({"question": "How do I fix my car engine?"})["kind"] == "not_found"


@step("Ask: medicine question is declined without calling the model")
def _():
    assert app.ask({"question": "Anong gamot sa sakit ng ulo?"})["kind"] == "medication"


@step("Ask: danger sign -> emergency screen, not a chat answer")
def _():
    assert app.ask({"question": "may pagdurugo ako ngayon"})["kind"] == "emergency"


@step("Nearby: facilities sorted by distance")
def _():
    d = [f["distance_km"] for f in app.facilities(None, None, None)["facilities"]]
    assert d and d == sorted(d), d


@step("Web server starts, serves the app and /api/health over localhost")
def _():
    httpd = server.serve(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert b"<title>PTP</title>" in urllib.request.urlopen(base + "/").read()
        health = json.load(urllib.request.urlopen(base + "/api/health"))
        assert "mode" in health
    finally:
        httpd.shutdown()
        httpd.server_close()


@step("Speech-to-text reads a recording, and its model is downloaded (needed to record)")
def _():
    assert stt.backend(), "none installed - recording will not work (see README)"
    if stt.backend() != "faster-whisper":
        return stt.backend()
    import wave
    from faster_whisper import decode_audio
    from faster_whisper.utils import download_model
    tone = Path(tmp.name) / "check.wav"
    with wave.open(str(tone), "wb") as w:  # one second of silence, decoded the way recordings are
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(b"\0\0" * 16000)
    try:
        decode_audio(str(tone))
    except TypeError as exc:
        raise AssertionError(f"faster-whisper cannot read audio ({exc}): run  py -3.13 -m pip install -r requirements.txt")
    size = os.environ.get("PTP_WHISPER_MODEL", "small")
    try:
        download_model(size, local_files_only=True)
    except Exception:
        raise AssertionError(f"speech model '{size}' is not downloaded yet: run the download step in the README while online")
    return f"faster-whisper, model {size}"


print()
for ok, name, detail in results:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
failed = [r for r in results if not r[0]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed" + ("  (MOCK mode: this does not test AI quality)" if MOCK else ""))
tmp.cleanup()
sys.exit(1 if failed else 0)
