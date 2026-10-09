"""Ask: answer questions ONLY from guides stored on this device, with citations.

Flow:  safety rules first (no model)  ->  keyword retrieval (BM25, no model, no network)
       ->  local language model writes a short answer from the excerpts, or says NOT_FOUND
       ->  code checks the answer cites a real excerpt; otherwise "not found"
       ->  code keeps only the sentences that cite an excerpt and say what it says, in the
           question's language, and drops repeats; if none is left, the excerpt's own sentences
           are shown instead ("from_guide").
Small local models add facts of their own (foods, numbers of days) even when told not to, so
nothing they write reaches the user unless the guide supports it.

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
MIN_SUPPORT = 0.75    # share of the answer's words that must appear in the excerpts it cites
# A model that writes "not", "hindi" or "avoid" where the source has none has flipped its meaning.
NEGATIONS = set("""
not no never don dont cannot can't avoid stop without
hindi huwag wag bawal iwasan itigil wala walang
""".split())

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
Write the answer in {language}.
Use only what the excerpts say, in their own words where you can. Do not add foods, numbers, times, doses, diagnoses or advice that the excerpts do not state.
Cite the excerpt number after each sentence, like [2].
If the excerpts do not answer the question, reply with exactly: NOT_FOUND
Keep the answer to at most three short sentences, and do not repeat yourself."""

LANGUAGE_NAMES = {"en": "English", "tl": "Filipino (Tagalog)"}


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


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def adds_nothing(text: str, source: str, min_share: float) -> bool:
    """True if text says nothing the source does not: at least min_share of its words are in the
    source, and every number and every negation in it is in the source too."""
    words, known = tokens(text), set(tokens(source))
    if not words or sum(w in known for w in words) / len(words) < min_share:
        return False
    if not set(re.findall(r"\d+", text)) <= set(re.findall(r"\d+", source)):
        return False
    raw = lambda t: set(re.findall(r"[\w']+", t.casefold()))
    return not ((raw(text) & NEGATIONS) - raw(source))


def is_supported(reply: str, sources: list[str], lang: str) -> bool:
    """True if the reply adds nothing to the sources and is in the question's language."""
    body = re.sub(r"\[\d+\]", " ", reply)
    return adds_nothing(body, " ".join(sources), MIN_SUPPORT) and language_of(body) == lang


def _cited_sentences(reply: str) -> list[str]:
    """Sentences of a reply, each with its citation even when written after the full stop."""
    out: list[str] = []
    for part in _sentences(reply):
        if out and re.fullmatch(r"(\[\d+\][\s.,]*)+", part):
            out[-1] += " " + part.strip()
        else:
            out.append(part)
    return out


def supported_sentences(reply: str, hits: list[dict], lang: str) -> list[str]:
    """The reply's sentences that cite a real excerpt and add nothing to it, without repeats."""
    kept: list[str] = []
    for sentence in _cited_sentences(reply):
        cited = {int(n) for n in re.findall(r"\[(\d+)\]", sentence)}
        if not cited or not all(1 <= n <= len(hits) for n in cited):
            continue
        if not is_supported(sentence, [hits[n - 1]["text"] for n in cited], lang):
            continue
        if any(_overlap(sentence, earlier) >= 0.6 for earlier in kept):
            continue  # says again what an earlier sentence said
        kept.append(sentence)
    return kept


def _overlap(a: str, b: str) -> float:
    """Share of words two sentences have in common (0 to 1), citations left out."""
    wa, wb = (set(tokens(re.sub(r"\[\d+\]", " ", x))) for x in (a, b))
    return len(wa & wb) / max(1, len(wa | wb))


def guide_sentences(hit: dict, lang: str) -> str:
    """The excerpt's own sentences in the question's language (the sample guides are bilingual)."""
    sentences = _sentences(hit["text"])
    return " ".join([s for s in sentences if language_of(s) == lang] or sentences)


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
    system = ANSWER_SYSTEM.format(language=LANGUAGE_NAMES[lang])
    reply = llm.chat("answer", system, f"EXCERPTS:\n{excerpts}\n\nQUESTION: {question}").strip()

    cited = [int(n) for n in re.findall(r"\[(\d+)\]", reply)]
    if "NOT_FOUND" in reply or not cited or any(n < 1 or n > len(hits) for n in cited):
        return _result("not_found", lang, add_to_questions=True)

    kept = supported_sentences(reply, hits, lang)
    from_guide = not kept
    if from_guide:  # nothing the model wrote is backed by the guide: show the guide itself instead
        cited = [cited[0]]
        reply = f"{guide_sentences(hits[cited[0] - 1], lang)} [{cited[0]}]"
    else:
        reply = " ".join(kept)
        cited = [int(n) for n in re.findall(r"\[(\d+)\]", reply)]

    citations = []
    for n in sorted(set(cited)):
        h = hits[n - 1]
        citations.append({"n": n, "title": h["title"], "heading": h["heading"], "source": h["source"],
                          "publisher": h["publisher"], "sample": h["sample"]})
    return _result("answer", lang, answer=reply, citations=citations, from_guide=from_guide,
                   sample_guides=any(c["sample"] for c in citations))
