# PTP — a pregnancy companion that works with Wi-Fi off

Built for the AppBuildersPH Hackathon 2026 (theme: Local AI). Everything runs on one laptop: no cloud, no accounts, no internet after setup.

| Screen | What it does |
|---|---|
| **Tala ng Konsulta** (from **Record a visit** on Home) | Record (with consent) or paste a doctor/midwife visit. A local speech model writes the transcript; a local language model proposes a summary and tasks. **Code checks the AI**: a task is kept only if its quote appears in the transcript and is not a question; dates ("susunod na Huwebes, alas nuwebe") are worked out by code, not guessed by the model; a return visit with a clear date that the model missed is added by code (and labelled so); the summary is hidden if it uses words, numbers or negations the transcript does not. You tick what to keep. |
| **Kalendaryo** | Month view, due date, trimester bar, upcoming tasks. Due date = LMP + 280 days (an estimate, shown as one). |
| **Tanong (Ask)** | Answers only from the pregnancy guide stored on the device (about 50 topics in Tagalog and English: check-ups, food and drink, common discomforts, exercise and sleep, baby's movements, danger signs, labour, after birth), with a citation. **Code checks every sentence** of the model's answer against the guide passage it cites (words, numbers, negations, language) and drops the rest; if nothing survives, the app quotes the guide itself. Medicine and "is this normal?" questions get a fixed refusal; danger-sign words go to Emergency. If no guide covers it, it says so and offers a button to save the question for the midwife. |
| **Malapit** | Facilities sorted by distance, with a schematic offline map. |
| **Emergency** (red button in the middle of the tab bar) | `tel:911`, nearest facility, danger-sign list. |
| **Buod para sa midwife** | One printable page: week, due date, confirmed tasks, saved questions. This replaces online consultation. |

## Language

The app opens in **English**. Users can switch to **Tagalog** with the language buttons on the first setup screen or the `EN | TL` switch on Home; the browser remembers the choice. Only the app's own text changes: AI answers and visit notes stay in the language the question or visit was in, so a question typed in Tagalog gets a Tagalog answer either way.

All screen text is in `web/strings.json`, one block per language. To fix a translation, edit that file; `tests/test_strings.py` fails if a line is missing in either language or its `{placeholders}` differ.

## Run it

The app itself needs only Python 3.10+ (standard library). Recording visits also needs `pip install -r requirements.txt` (use Python 3.11-3.14; 3.13 is what we test).

```
python3 -m ptp.server          # open http://127.0.0.1:8765
python3 -m unittest discover -s tests -t .      # 76 tests
python3 demo_check.py              # real local model, Wi-Fi OFF  <- run this before the demo
python3 demo_check.py --mock       # app code only; NOT AI
```

`PTP_MOCK=1` swaps the AI for a rule-based stand-in so the app runs without a model. It is **not AI**; the UI shows a yellow banner whenever it is active. Never demo with it and call it AI.

## Before you go offline (do this once, with internet)

1. **Language model** — install [Ollama](https://ollama.com), then `ollama pull gemma3:4b` (the default; about 3.3 GB). See "Choosing the model" below for why, and `PTP_LLM_MODEL=<model>` to use another.
2. **Speech to text** — `pip install -r requirements.txt` (faster-whisper, with PyAV kept below 19: faster-whisper 1.2.1 cannot read audio with PyAV 19, so a plain `pip install faster-whisper` breaks recording), then download the speech model once: `python3 -c "from faster_whisper import download_model; download_model('small')"` (`PTP_WHISPER_MODEL=small`; set `PTP_STT_LANGUAGE=tl` to force Tagalog). whisper.cpp also works (`PTP_WHISPER_CLI`, `PTP_WHISPER_MODEL_PATH`, plus `ffmpeg`). Without it, recording is unavailable and you paste the transcript. **Tagalog/Taglish quality on clinic audio is untested** — record a staged visit and look at it.
3. Run `python3 demo_check.py`, then turn Wi-Fi off and run it again. It now decodes a test sound and checks the speech model is downloaded, not just installed.

## Choosing the model

We ran four small local models through `compare_models.py`: three staged visits (Tagalog, English, Taglish) and eleven Ask questions, with every code check above switched on. This is our own small test set on a CPU-only test machine, not a benchmark: run it again on your laptop with `python3 compare_models.py gemma3:4b llama3.2:3b`.

| Model | To-dos found | Extra or rejected to-dos | Return dates right | Ask right | Visit summaries shown |
|---|---|---|---|---|---|
| **Gemma 3 4B** (`gemma3:4b`, default) | 8/9 | 0 | 3/3 | 10/11 | 3/3, all accurate |
| Llama 3.2 3B (`llama3.2:3b`) | 8/9 | 3, incl. a to-do titled "Pagkain iwasan" (foods to avoid) the midwife never gave | 3/3 | 10/11 | 2/3 |
| Qwen3 4B Instruct 2507 | 9/9 | 0 | 3/3 | 9/11 | 0/3 (all hidden by the check) |
| Ministral 3 3B | 9/9 | 0 | 3/3 | 8/11 | 2/3 |

Before the code checks, the models made dangerous mistakes: Llama 3.2 3B (which does not officially support Tagalog) answered with foods and check-up intervals the guide never mentions, and turned "Can I still drink coffee?" into a task titled "Coffee allowed"; Qwen3 and Ministral summarised "keep taking your folic acid" as "reduce the folic acid". Those are why the checks exist. All return dates are 3/3 because code adds a dated return visit the model missed.

## Windows 11 (PowerShell)

```
py -3.13 -m pip install -r requirements.txt
py -3.13 -m ptp.server
py -3.13 -m unittest discover -s tests -t .
py -3.13 demo_check.py
$env:PTP_LLM_MODEL = "llama3.2:3b"; py -3.13 -m ptp.server     # env vars last for this window only
py -3.13 compare_models.py gemma3:4b llama3.2:3b                # scores + time per model, on YOUR laptop
```

Install Python 3.13 from python.org (tick "Add to PATH"); Python 3.15 has no Windows packages yet for the speech-to-text libraries, and `py -3.13` picks 3.13 even if a newer Python is installed. Then install Ollama for Windows. On a 16 GB laptop without a dedicated GPU, stick to models of about 4B parameters or smaller and the `small` or `base` Whisper model; larger ones will be slow.

**Laptop with an NVIDIA GPU and 16 GB RAM (e.g. RTX 5050 8 GB, i5-13420H):** run it this way, not with Docker. Ollama for Windows puts the language model on the GPU's own memory, so it barely touches system RAM; Docker Desktop's Ollama can only use the CPU and adds a virtual machine on top. Keep to models of 3-4B (the default `gemma3:4b` is about 3.3 GB of the GPU's 8 GB), keep the NVIDIA driver up to date, and close browser tabs and other apps before a demo if Task Manager shows memory above about 80%. Speech-to-text stays on the CPU (4 threads) by default; `PTP_WHISPER_DEVICE=cuda` moves it to the GPU, but only after installing NVIDIA cuBLAS and cuDNN for CUDA 12, because without them the app can crash on the first recording. Speeds here are not measured: `compare_models.py` prints them on the real machine. Windows is untested on our side: the code uses no Windows-specific calls, but treat the first run as a test.

## Run with Docker (Windows, Mac or Linux)

Runs the app and the local AI model server (Ollama) together, so you don't install Python or Ollama. Needs Docker Desktop (Windows, Mac) or Docker Engine (Linux).

```
docker compose up -d --build                          # first time, WITH internet
docker compose logs -f ollama-pull whisper-download   # wait until both models are downloaded (a few GB, once)
```

Open http://localhost:8765. After that first run everything works with Wi-Fi off: `docker compose up -d` starts it, `docker compose down` stops it, and profile, tasks, recordings and models stay in Docker volumes. If you forget the download, the app says "The AI model … is not downloaded yet" instead of answering.

- **Another model:** put `PTP_LLM_MODEL=llama3.2:3b` in a `.env` file next to `compose.yaml` and run `docker compose up -d` while online; it downloads on start. Same for `PTP_WHISPER_MODEL=base`.
- **Phones on the same Wi-Fi or hotspot:** add `PTP_BIND=0.0.0.0` to `.env`, restart, and open `http://<laptop-ip>:8765` on the phone. By default only the laptop itself can open the app.
- **Speed and memory:** Ollama inside Docker runs on the CPU and uses system RAM. If the laptop has an NVIDIA GPU, install Ollama for Windows instead, set `PTP_LLM_URL=http://host.docker.internal:11434/v1` in `.env`, and start only the app: `docker compose up -d --no-deps app whisper-download`. On a 16 GB laptop, running without Docker (see Windows 11 above) is lighter still.
- **Checks:** `docker compose exec app python demo_check.py` (offline check, as above) and `docker compose run --rm --no-deps app python -m unittest discover -s tests -t .`
- **Smaller image without recording:** `docker build --build-arg STT=false -t ptp .` (paste transcripts instead).

## Environment variables

`PTP_MOCK`, `PTP_LLM_URL`, `PTP_LLM_MODEL`, `PTP_LLM_KEEP_ALIVE` (how long Ollama keeps the model loaded after PTP starts; default `8h`, so the first answer in a demo is not slow), `PTP_WHISPER_MODEL`, `PTP_WHISPER_DEVICE` (`cpu` default, or `cuda`), `PTP_WHISPER_CLI`, `PTP_WHISPER_MODEL_PATH`, `PTP_STT_LANGUAGE`, `PTP_STATE_DIR` (where profile, tasks and recordings are saved; default `./state`), `PTP_TODAY=YYYY-MM-DD` (pretend today is this date, for staging), `PTP_HOST` / `PTP_PORT` (default `127.0.0.1:8765`), `PTP_LOG=1`.

## What you must replace before this is real

- `data/guides/pregnancy_guide.md` — our own summary of WHO and NHS advice (sources per topic in `data/GUIDE_SOURCES.md`), **not reviewed by a health worker** (front matter says `sample: true`; the UI labels every answer from it). A doctor or midwife must review it, including the Tagalog; add Philippine DOH material where it differs. Real guides go in `data/guides/` with `source`, `publisher`, `retrieved`, `license` in the front matter; delete the `sample` line only after review. Check each license first.
  Format: one paragraph per topic under `##` headings, Tagalog then English, and a hidden `<!-- keywords: ... -->` line listing the everyday words people use for that topic (both languages), so search finds it. `tests/test_guides.py` checks the format and that 20 everyday questions find the right paragraph.
- `data/red_flags.json` — an **unreviewed placeholder** (`"reviewed": false`; Emergency shows a warning). A doctor or midwife must review it, then set `reviewed` to `true`. A test fails if you flip it without thinking, on purpose.
- `data/facilities.json` — three **fictional** facilities. Replace with a real list and its source.

## Known weak spots

- **Retrieval is keyword search (BM25)**, not meaning. Each guide paragraph carries a hidden list of everyday words for its topic ("manas", "kape", "kick") to help, but a question using none of them misses it (fails safe: "not found"). A question that shares only a common word with the guide reaches the model, which answered `NOT_FOUND` in our tests ("Is papaya safe to eat?").
- **The code checks are word-level**: they catch added words, numbers and negations, not every change of meaning. The visit summary is labelled as written by AI for that reason.
- **The guide decides what Ask can answer.** In our last run with Gemma 3 4B, 27 of 30 test questions got an answer, 7 of them by quoting the guide (questions in `tests/test_guides.py`); the other three were a car-engine question, papaya (not in the guide), and "Ilang kilo ang dapat kong itaba?", which search finds now. topics the guide does not cover ("Is papaya safe to eat?") get "not found" and the button to save the question. Add a paragraph to cover a new topic. When most of the model's sentences fail the check, Ask shows the guide's own paragraph instead, because what is left can be half an answer.
- **Microphone**: browsers allow it only on `localhost` or HTTPS. Use the laptop's own browser. Opening the app from a phone over a hotspot (`PTP_HOST=0.0.0.0`) works for everything except recording.
- The map is a schematic drawing of distances, not street tiles.
- State is one JSON file; no encryption. Recordings stay in `state/recordings/`. Delete them if the consultation was real.
- Tested with real local models (see "Choosing the model") and with recordings from Chromium's test microphone decoded by faster-whisper. Not tested: the Whisper model on real clinic audio, phones, browsers other than Chromium, Windows, accessibility tools.

## Safety design in one paragraph

Safety rules run before any model. The model only proposes; code verifies (quote grounding, questions are never tasks, date math, citation check that fails closed, every Ask sentence checked against its source, summaries hidden when unsupported). Medicines found in a visit are tagged "verify with your doctor/midwife". The app never diagnoses, never advises on medicines or doses, and every answer screen says it does not replace a doctor or midwife.

## Layout

`ptp/` core (dates, grounding, safety, geo, rag, extract, llm, stt, store, server) · `web/` the UI (vanilla JS/CSS, no external files; screen text in `strings.json`) · `data/` guides, red flags, facilities, sample visit · `tests/` · `demo_check.py`
