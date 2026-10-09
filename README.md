# PTP — a pregnancy companion that works with Wi-Fi off

Built for the AppBuildersPH Hackathon 2026 (theme: Local AI). Everything runs on one laptop: no cloud, no accounts, no internet after setup.

| Screen | What it does |
|---|---|
| **Tala ng Konsulta** | Record (with consent) or paste a doctor/midwife visit. A local speech model writes the transcript; a local language model proposes a summary and tasks. **Code checks the AI**: a task is kept only if its quote appears in the transcript, and dates ("susunod na Huwebes, alas nuwebe") are worked out by code, not guessed by the model. You tick what to keep. |
| **Kalendaryo** | Month view, due date, trimester bar, upcoming tasks. Due date = LMP + 280 days (an estimate, shown as one). |
| **Tanong (Ask)** | Answers only from guides stored on the device, with a citation. Medicine and "is this normal?" questions get a fixed refusal; danger-sign words go to Emergency. If no guide covers it, it says so and saves the question for the midwife. |
| **Malapit** | Facilities sorted by distance, with a schematic offline map. |
| **Emergency** | `tel:911`, nearest facility, danger-sign list. |
| **Buod para sa midwife** | One printable page: week, due date, confirmed tasks, saved questions. This replaces online consultation. |

## Language

The app opens in **English**. Users can switch to **Tagalog** with the language buttons on the first setup screen or the `EN | TL` switch on Home; the browser remembers the choice. Only the app's own text changes: AI answers and visit notes stay in the language the question or visit was in, so a question typed in Tagalog gets a Tagalog answer either way.

All screen text is in `web/strings.json`, one block per language. To fix a translation, edit that file; `tests/test_strings.py` fails if a line is missing in either language or its `{placeholders}` differ.

## Run it

Requires Python 3.10+ and nothing else (standard library only).

```
python3 -m ptp.server          # open http://127.0.0.1:8765
python3 -m unittest discover -s tests -t .      # 50 tests
python3 demo_check.py              # real local model, Wi-Fi OFF  <- run this before the demo
python3 demo_check.py --mock       # app code only; NOT AI
```

`PTP_MOCK=1` swaps the AI for a rule-based stand-in so the app runs without a model. It is **not AI**; the UI shows a yellow banner whenever it is active. Never demo with it and call it AI.

## Before you go offline (do this once, with internet)

1. **Language model** — install [Ollama](https://ollama.com), then `ollama pull <model>` and set `PTP_LLM_MODEL=<model>`. The default `llama3.2:3b` is a placeholder; **not tested here**. Try a few models on your laptop with the sample visit and pick by results.
2. **Speech to text** — `pip install faster-whisper` and run once so the model downloads (`PTP_WHISPER_MODEL=small`; set `PTP_STT_LANGUAGE=tl` to force Tagalog), or use whisper.cpp (`PTP_WHISPER_CLI`, `PTP_WHISPER_MODEL_PATH`, plus `ffmpeg`). Without it, recording is unavailable and you paste the transcript. **Tagalog/Taglish quality on clinic audio is untested** — record a staged visit and look at it.
3. Run `python3 demo_check.py`, then turn Wi-Fi off and run it again.

## Windows 11 (PowerShell)

```
py -m ptp.server
py -m unittest discover -s tests -t .
py demo_check.py
$env:PTP_LLM_MODEL = "qwen2.5:3b"; py -m ptp.server     # env vars last for this window only
py compare_models.py llama3.2:3b qwen2.5:3b gemma3:4b           # time + results per model, on YOUR laptop
```

Install Python 3.10+ from python.org (tick "Add to PATH"), Ollama for Windows, and `py -m pip install faster-whisper`. On a 16 GB laptop without a dedicated GPU, stick to models of about 4B parameters or smaller and the `small` or `base` Whisper model; larger ones will be slow. Speeds here are not measured: `compare_models.py` prints them on the real machine. Windows is untested on our side: the code uses no Windows-specific calls, but treat the first run as a test.

## Environment variables

`PTP_MOCK`, `PTP_LLM_URL`, `PTP_LLM_MODEL`, `PTP_WHISPER_MODEL`, `PTP_WHISPER_CLI`, `PTP_WHISPER_MODEL_PATH`, `PTP_STT_LANGUAGE`, `PTP_STATE_DIR` (where profile, tasks and recordings are saved; default `./state`), `PTP_TODAY=YYYY-MM-DD` (pretend today is this date, for staging), `PTP_HOST` / `PTP_PORT` (default `127.0.0.1:8765`), `PTP_LOG=1`.

## What you must replace before this is real

- `data/guides/*.md` — the bundled guide is a **sample**, not official guidance (front matter says `sample: true`; the UI labels citations from it). Add real, licensed guides (e.g. DOH material) with `source`, `publisher`, `retrieved`, `license` in the front matter, and delete the `sample` line. Check each license first.
- `data/red_flags.json` — an **unreviewed placeholder** (`"reviewed": false`; Emergency shows a warning). A doctor or midwife must review it, then set `reviewed` to `true`. A test fails if you flip it without thinking, on purpose.
- `data/facilities.json` — three **fictional** facilities. Replace with a real list and its source.

## Known weak spots

- **Retrieval is keyword search (BM25)**, not meaning. A question using different words than the guide misses it (fails safe: "not found"). With the mock, an off-topic question that shares a keyword can still return an unrelated excerpt; a real model is instructed to answer `NOT_FOUND` there, but this has not been tested with a real model.
- **Microphone**: browsers allow it only on `localhost` or HTTPS. Use the laptop's own browser. Opening the app from a phone over a hotspot (`PTP_HOST=0.0.0.0`) works for everything except recording.
- The map is a schematic drawing of distances, not street tiles.
- State is one JSON file; no encryption. Recordings stay in `state/recordings/`. Delete them if the consultation was real.
- Not tested: real LLM/STT output, phones, other browsers than Chromium, accessibility tools.

## Safety design in one paragraph

Safety rules run before any model. The model only proposes; code verifies (quote grounding, date math, citation check that fails closed). Medicines found in a visit are tagged "verify with your doctor/midwife". The app never diagnoses, never advises on medicines or doses, and every answer screen says it does not replace a doctor or midwife.

## Layout

`ptp/` core (dates, grounding, safety, geo, rag, extract, llm, stt, store, server) · `web/` the UI (vanilla JS/CSS, no external files; screen text in `strings.json`) · `data/` guides, red flags, facilities, sample visit · `tests/` · `demo_check.py`
