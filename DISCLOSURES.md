# Disclosures — PTP

**AI assistance.** This project was designed and written with Claude (Anthropic) through Claude Code during the hackathon, including the code, tests, UI and documentation. The team is responsible for what is submitted.

**Code reuse.** No code was copied from other projects. Ideas only: Project NOMAD (Apache-2.0) and glebis/claude-skills (MIT) were read for inspiration. The "a model's quote must appear in the source" check (`ptp/grounding.py`) was first written for an earlier idea of ours (Hudyat) on Build Day and reused here — that idea was dropped; nothing else carried over.

**Runtime models and libraries (all run on the laptop):**
- Language model: Gemma 3 4B (`gemma3:4b`, about 3.9B parameters, Q4_K_M), Google, under the Gemma Terms of Use; run through Ollama (MIT). Chosen after comparing it with Llama 3.2 3B (Llama 3.2 Community License), Qwen3 4B Instruct 2507 and Ministral 3 3B on our own test visits; see README, "Choosing the model". Any of them can be used instead with `PTP_LLM_MODEL`.
- Speech-to-text: OpenAI Whisper "small" (244M parameters, MIT), in Systran's CTranslate2 version (`Systran/faster-whisper-small`), run by faster-whisper 1.2.1 (MIT) with PyAV for audio decoding. Silence is skipped with Silero VAD (MIT), bundled in faster-whisper. whisper.cpp is supported as an alternative.
- Ask uses BM25 keyword search written for PTP; there is no embedding model.
- The app itself uses only the Python standard library and vanilla JS. No CDN, fonts or web calls. Docker images used by the optional Docker setup: `python:3.13.15-slim-bookworm`, `ollama/ollama:0.34.4`.

**Data.** `data/guides/sample_guide.md` is generic sample text written for testing, not official guidance. `data/facilities.json` is fictional. `data/sample_transcript.txt` is a fictional, staged conversation. `data/red_flags.json` is an unreviewed placeholder. None of these are medical sources.

**Mock mode.** `PTP_MOCK=1` / `demo_check.py --mock` use a rule-based stand-in, not AI. It exists only for tests and is labelled in the UI.

**Benchmarks.** None claimed. The model comparison in the README is our own small test set (three staged visits, eleven questions) run once on a CPU-only test machine, reproducible with `compare_models.py`; it is for choosing a model for this app, not a measure of model accuracy. The unit tests describe the surrounding code, not the models.

**Demo.** Any recording shown is a staged, fictional consultation with consent. The Wi-Fi-off demo is run live on the laptop.

**Not medical advice.** PTP does not diagnose or advise on medicines. It is a prototype and has not been clinically reviewed.
