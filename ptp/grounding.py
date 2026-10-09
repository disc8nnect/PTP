"""Quote grounding: a model-proposed quote counts only if it really appears in the source text.

Reused idea from the Hudyat helper written earlier on Build Day (see DISCLOSURES.md).
No models, no network.
"""
from __future__ import annotations

import re
import unicodedata

_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s).casefold()
    s = _PUNCT.sub(" ", s)
    return " ".join(s.split())


def quote_is_grounded(quote: str, source: str) -> bool:
    """True if the quote (ignoring case, punctuation and extra spaces) is a substring of source."""
    q = _norm(quote or "")
    return bool(q) and q in _norm(source)


def keep_grounded(items: list[dict], source: str, field: str = "quote") -> tuple[list[dict], list[dict]]:
    """Split items into (kept, rejected) by whether item[field] is really in the source."""
    kept, rejected = [], []
    for item in items:
        (kept if quote_is_grounded(item.get(field, ""), source) else rejected).append(item)
    return kept, rejected
