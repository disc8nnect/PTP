"""Local language model access.

OllamaLLM talks to any OpenAI-compatible server running ON THIS MACHINE (Ollama, llama.cpp
server, LM Studio). It uses only the standard library, so no packages are needed.

MockLLM is a rule-based stand-in used ONLY by the tests and by `demo_check.py --mock`. It is
not an AI model and must never be shown as one: the server reports mode "mock" and the web UI
shows a banner whenever it is active. Turn it on with PTP_MOCK=1.

Environment:
  PTP_LLM_URL    default http://127.0.0.1:11434/v1
  PTP_LLM_MODEL  default llama3.2:3b   (a placeholder: test models on your own laptop)
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


# Talk to the local model directly, never through a system/corporate proxy (common on Windows).
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class LLMUnavailable(RuntimeError):
    """The local model server cannot be reached."""


class OllamaLLM:
    name = "ollama"

    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: float = 180.0):
        self.base_url = (base_url or os.environ.get("PTP_LLM_URL") or "http://127.0.0.1:11434/v1").rstrip("/")
        self.model = model or os.environ.get("PTP_LLM_MODEL") or "llama3.2:3b"
        self.timeout = timeout

    def available(self) -> bool:
        try:
            with _OPENER.open(self.base_url + "/models", timeout=2) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def chat(self, task: str, system: str, user: str, json_mode: bool = False) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _OPENER.open(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload["choices"][0]["message"]["content"]
        except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError) as exc:
            raise LLMUnavailable(f"Local model not reachable at {self.base_url}: {exc}") from exc


# ----------------------------------------------------------------------------- mock

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_LABEL = re.compile(r"^[A-Za-zÑñ][A-Za-zÑñ ]{0,19}:\s*")
_PATIENT = re.compile(r"^(maria|pasyente|patient|buntis)\s*:", re.IGNORECASE)


def _sentences(transcript: str) -> list[tuple[str, bool]]:
    """(sentence without speaker label, spoken by patient) for each sentence."""
    out = []
    for line in transcript.splitlines():
        line = line.strip()
        if not line:
            continue
        patient = bool(_PATIENT.match(line))
        line = _LABEL.sub("", line)
        for s in _SENTENCE_SPLIT.split(line):
            s = s.strip()
            if s:
                out.append((s, patient))
    return out


class MockLLM:
    """Keyword rules shaped like model output. NOT an AI. For tests and wiring checks only."""

    name = "mock"

    def available(self) -> bool:
        return True

    def chat(self, task: str, system: str, user: str, json_mode: bool = False) -> str:
        if task == "extract":
            return self._extract(user)
        if task == "answer":
            return self._answer(user)
        return ""

    @staticmethod
    def _extract(transcript: str) -> str:
        tasks, questions = [], []
        for s, patient in _sentences(transcript):
            low = s.lower()
            if patient:
                if s.endswith("?"):
                    questions.append({"question": s, "quote": s})
                continue
            if re.search(r"blood test|lab test|ultrasound|urine|laboratory", low):
                kind = "test"
            elif re.search(r"\b(iron|tablet|vitamin|bitamina|gamot|medicine)\b", low):
                kind = "medicine"
            elif re.search(r"\b(balik|bumalik|return|follow[- ]?up|susunod na)\b", low):
                kind = "appointment"
            else:
                continue
            tasks.append({"title": s[:70].rstrip(" ,.;"), "kind": kind, "quote": s.rstrip(".")})
        summary = f"May {len(tasks)} bagay na binanggit sa usapan." if tasks else ""
        return json.dumps({"summary": summary, "tasks": tasks, "unanswered_questions": questions}, ensure_ascii=False)

    @staticmethod
    def _answer(user: str) -> str:
        m = re.search(r"\[1\]\s*(.+?)(?:\n|$)", user)
        if not m:
            return "NOT_FOUND"
        first = re.split(r"(?<=[.!?])\s+", m.group(1).strip())[0]
        return f"{first} [1]"


def get_llm():
    if os.environ.get("PTP_MOCK") == "1":
        return MockLLM()
    return OllamaLLM()
