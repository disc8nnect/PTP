# Disclosures — PTP

**AI assistance.** This project was designed and written with Claude (Anthropic) through Claude Code during the hackathon, including the code, tests, UI and documentation. The team is responsible for what is submitted.

**Code reuse.** No code was copied from other projects. Ideas only: Project NOMAD (Apache-2.0) and glebis/claude-skills (MIT) were read for inspiration. The "a model's quote must appear in the source" check (`ptp/grounding.py`) was first written for an earlier idea of ours (Hudyat) on Build Day and reused here — that idea was dropped; nothing else carried over.

**Runtime models and libraries (to be filled in once chosen and tested):**
- Language model: _name, size, license — TBD_ (run through Ollama or another local OpenAI-compatible server)
- Speech-to-text: _faster-whisper / whisper.cpp + model name and license — TBD_
- The app itself uses only the Python standard library and vanilla JS. No CDN, fonts or web calls.

**Data.** `data/guides/sample_guide.md` is generic sample text written for testing, not official guidance. `data/facilities.json` is fictional. `data/sample_transcript.txt` is a fictional, staged conversation. `data/red_flags.json` is an unreviewed placeholder. None of these are medical sources.

**Mock mode.** `PTP_MOCK=1` / `demo_check.py --mock` use a rule-based stand-in, not AI. It exists only for tests and is labelled in the UI.

**Benchmarks.** None claimed. Test results in this repo describe the surrounding code, not model accuracy.

**Demo.** Any recording shown is a staged, fictional consultation with consent. The Wi-Fi-off demo is run live on the laptop.

**Not medical advice.** PTP does not diagnose or advise on medicines. It is a prototype and has not been clinically reviewed.
