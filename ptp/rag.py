"""Ask: answer questions ONLY from guides stored on this device, with citations.

Flow:  safety rules first (no model)  ->  keyword retrieval (BM25, no model, no network)
       ->  local language model writes a short answer from the excerpts
       ->  code checks the answer cites a real excerpt; otherwise "not found".

Retrieval is plain keyword search on purpose: it needs no embedding model, runs instantly and
is easy to test. Its weakness is vocabulary: a question in Filipino only finds a guide that
uses the same words, which is why the sample guide is bilingual. An embedding model is the
upgrade path.

Guide files (data/guides/*.md) start with a front-matter block:
    ---
    title: ...
    source: link or document name
    publisher: ...
    retrieved: YYYY-MM-DD
    license: ...
    sample: true        <- delete this line for real, vetted guides
    ---
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from . import safety

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MIN_SCORE = 0.3       # below this, a chunk does not count as a match
MIN_COVERAGE = 0.5    # at least this share of the question's keywords must appear in the chunk
TOP_K = 3

STOPWORDS = set("""
a an the of to in on at for and or is are was be it this that with as by from i my me you your we
do does can could should would will what which who how when where why not no yes
ang ng sa mga na at o ay ko mo ako ka siya kami tayo ba po opo hindi oo may mayroon para kung
anong ano paano bakit kailan saan sino akin ating namin natin dapat puwede pwede ito iyon yan yun kong
""".split())

LANG_MARKERS = set("""
ang ng sa mga ako ba po ko mo anong ano paano bakit kailan saan puwede pwede dapat akin namin natin
kumain gamot uminom may nang kayo ninyo
""".split())

TEXT = {
    "not_found": {
        "tl": "Wala akong makitang sagot sa mga gabay na nasa telepono. Itanong ito sa doktor o midwife.",
        "en": "I can't find an answer in the guides stored on this device. Please ask your doctor or midwife.",
    },
    "medication": {
        "tl": "Hindi ako makapagbibigay ng payo tungkol sa gamot. Itanong ito sa doktor o midwife.",
        "en": "I can't give advice about medicines. Please ask your doctor or midwife.",
    },
    "diagnosis": {
        "tl": "Hindi ko matutukoy kung normal o delikado ito. Kausapin ang doktor o midwife.",
        "en": "I can't tell whether this is normal or dangerous. Please talk to your doctor or midwife.",
    },
    "emergency": {
        "tl": "Maaaring kailangan mo ng agarang tulong. Tumawag sa 911 o pumunta agad sa pinakamalapit na ospital. Buksan ang Emergency.",
        "en": "You may need help right away. Call 911 or go to the nearest hospital now. Open Emergency.",
    },
}

ANSWER_SYSTEM = """Answer the question using ONLY the numbered excerpts provided.
Cite the excerpt number after each claim, like [2].
If the excerpts do not contain the answer, reply with exactly: NOT_FOUND
Do not give medical advice, doses or diagnoses that are not in the excerpts.
Keep the answer to three short sentences. Reply in the language of the question (Filipino, English or Taglish)."""


def _stem(t: str) -> str:
    """Tiny plural fix for English ("fruits" -> "fruit"). Filipino words are left alone."""
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t


def tokens(text: str) -> list[str]:
    words = re.findall(r"\w+", (text or "").casefold())
    return [_stem(t) for t in words if len(t) > 1 and t not in STOPWORDS]


def language_of(text: str) -> str:
    words = set(re.findall(r"\w+", (text or "").casefold()))
    return "tl" if words & LANG_MARKERS else "en"


# ----------------------------------------------------------------------------- guides


def _parse_front_matter(raw: str) -> tuple[dict, str]:
    meta: dict = {}
    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
            body = parts[2]
    return meta, body


def load_guides(folder: Path | None = None) -> list[dict]:
    """Chunks of every guide: one chunk per paragraph, with its heading and source details."""
    folder = Path(folder) if folder else DATA_DIR / "guides"
    chunks: list[dict] = []
    for path in sorted(folder.glob("*.md")):
        meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
        heading = meta.get("title", path.stem)
        for block in re.split(r"\n\s*\n", body.strip()):
            block = block.strip()
            if not block:
                continue
            if block.startswith("#"):
                heading = block.lstrip("#").strip().splitlines()[0]
                block = "\n".join(block.splitlines()[1:]).strip()
                if not block:
                    continue
            chunks.append({
                "id": len(chunks) + 1,
                "doc": path.stem,
                "title": meta.get("title", path.stem),
                "heading": heading,
                "text": block,
                "source": meta.get("source", ""),
                "publisher": meta.get("publisher", ""),
                "sample": meta.get("sample", "").lower() == "true",
            })
    return chunks


def retrieve(question: str, chunks: list[dict], k: int = TOP_K) -> list[dict]:
    """BM25 over the chunks. Returns up to k chunks (with "score") that clear MIN_SCORE."""
    q = set(tokens(question))
    if not q or not chunks:
        return []
    docs = [tokens(c["heading"] + " " + c["text"]) for c in chunks]
    n = len(docs)
    avg = sum(len(d) for d in docs) / n or 1.0
    df = {t: sum(1 for d in docs if t in d) for t in q}
    scored = []
    for chunk, d in zip(chunks, docs):
        score = 0.0
        matched = 0
        for t in q:
            tf = d.count(t)
            if not tf:
                continue
            matched += 1
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * (tf * 2.5) / (tf + 1.5 * (0.25 + 0.75 * len(d) / avg))
        if score >= MIN_SCORE and matched / len(q) >= MIN_COVERAGE:
            scored.append(dict(chunk, score=round(score, 3)))
    scored.sort(key=lambda c: -c["score"])
    return scored[:k]


# ----------------------------------------------------------------------------- answer


def _result(kind: str, lang: str, **extra) -> dict:
    base = {"kind": kind, "lang": lang, "citations": [], "add_to_questions": False}
    if kind in TEXT:
        base["answer"] = TEXT[kind][lang]
    base.update(extra)
    return base


def answer(question: str, chunks: list[dict], llm, flags: dict | None = None) -> dict:
    """Answer one question. kind is: answer | not_found | medication | diagnosis | emergency."""
    lang = language_of(question)
    verdict = safety.classify_question(question, flags)
    if verdict["kind"] == "emergency":
        return _result("emergency", lang, matched=verdict["matched"])
    if verdict["kind"] in ("medication", "diagnosis"):
        return _result(verdict["kind"], lang, add_to_questions=True)

    hits = retrieve(question, chunks)
    if not hits:
        return _result("not_found", lang, add_to_questions=True)

    excerpts = "\n".join(f"[{i}] {h['text']}" for i, h in enumerate(hits, start=1))
    reply = llm.chat("answer", ANSWER_SYSTEM, f"EXCERPTS:\n{excerpts}\n\nQUESTION: {question}").strip()

    cited = [int(n) for n in re.findall(r"\[(\d+)\]", reply)]
    if "NOT_FOUND" in reply or not cited or any(n < 1 or n > len(hits) for n in cited):
        return _result("not_found", lang, add_to_questions=True)

    citations = []
    for n in sorted(set(cited)):
        h = hits[n - 1]
        citations.append({"n": n, "title": h["title"], "heading": h["heading"], "source": h["source"],
                          "publisher": h["publisher"], "sample": h["sample"]})
    return _result("answer", lang, answer=reply, citations=citations,
                   sample_guides=any(c["sample"] for c in citations))
