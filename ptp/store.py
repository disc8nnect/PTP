"""On-device storage: one JSON file. Everything stays on this machine."""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def default_dir() -> Path:
    return Path(os.environ.get("PTP_STATE_DIR") or ROOT / "state")


class Store:
    def __init__(self, folder: Path | None = None):
        self.folder = Path(folder) if folder else default_dir()
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "recordings").mkdir(exist_ok=True)
        self.path = self.folder / "state.json"
        self._lock = threading.Lock()

    # -- raw
    def _load(self) -> dict:
        if not self.path.exists():
            return {"profile": {}, "tasks": [], "visits": [], "questions": []}
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def _save(self, state: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def snapshot(self) -> dict:
        with self._lock:
            return self._load()

    # -- profile
    def set_profile(self, name: str, lmp: date) -> dict:
        with self._lock:
            state = self._load()
            state["profile"] = {"name": name, "lmp": lmp.isoformat()}
            self._save(state)
            return state["profile"]

    # -- tasks
    def add_tasks(self, tasks: list[dict], visit_date: str, visit_summary: str = "") -> list[dict]:
        with self._lock:
            state = self._load()
            visit_id = uuid.uuid4().hex[:8]
            state["visits"].append({"id": visit_id, "date": visit_date, "summary": visit_summary})
            added = []
            for t in tasks:
                item = {
                    "id": uuid.uuid4().hex[:8],
                    "visit_id": visit_id,
                    "title": str(t.get("title", ""))[:200],
                    "kind": t.get("kind", "other"),
                    "quote": str(t.get("quote", ""))[:400],
                    "date": t.get("date"),
                    "time": t.get("time"),
                    "verify": bool(t.get("verify")),
                    "done": False,
                }
                state["tasks"].append(item)
                added.append(item)
            self._save(state)
            return added

    def toggle_task(self, task_id: str) -> dict | None:
        with self._lock:
            state = self._load()
            for t in state["tasks"]:
                if t["id"] == task_id:
                    t["done"] = not t["done"]
                    self._save(state)
                    return t
            return None

    def add_question(self, text: str) -> None:
        with self._lock:
            state = self._load()
            if text and text not in state["questions"]:
                state["questions"].append(text[:300])
                self._save(state)

    def next_appointment(self, today: date) -> dict | None:
        tasks = [t for t in self.snapshot()["tasks"]
                 if t["kind"] == "appointment" and t["date"] and t["date"] >= today.isoformat() and not t["done"]]
        tasks.sort(key=lambda t: (t["date"], t.get("time") or ""))
        return tasks[0] if tasks else None
