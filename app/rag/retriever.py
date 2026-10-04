import json
import re
from pathlib import Path

import chromadb

from app.rag.ingest import KEYWORDS

BASE = Path(__file__).parent.parent
DB_PATH = str(BASE.parent / "chroma_db")
DATA = BASE / "schemes" / "schemes.json"

_client = chromadb.PersistentClient(path=DB_PATH)
_col = _client.get_or_create_collection("schemes")
_schemes = json.loads(DATA.read_text(encoding="utf-8"))

# aam shabd jo match mein kaam ke nahi
STOP = {
    "a", "an", "the", "for", "of", "to", "in", "and", "or", "with", "is", "are",
    "government", "scheme", "schemes", "yojana", "pm", "india", "indian",
    "meri", "mera", "main", "mujhe", "hai", "hoon", "ho", "ka", "ke", "ki", "ko",
    "se", "me", "mein", "ye", "wo", "kya", "kaise", "kaunsi", "milegi", "milega",
    "umar", "saal", "income", "lakh", "chahiye", "batao", "aur", "par", "hi",
    "year", "years", "old", "below", "above", "low",
}


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z]+", text.lower())) - STOP


def _doc(s: dict) -> dict:
    return {"text": f"{s['name']}: {s['text']}", "name": s["name"], "url": s["url"]}


def _keyword_search(query: str, k: int) -> list:
    q = _tokens(query)
    scored = []
    for s in _schemes:
        kw = _tokens(KEYWORDS.get(s["name"], "") + " " + s["name"])
        score = len(q & kw)
        if score:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [_doc(s) for _, s in scored[:k]]


def _vector_search(query: str, k: int) -> list:
    res = _col.query(query_texts=[query], n_results=k)
    return [
        {"text": doc, "name": meta["name"], "url": meta["url"]}
        for doc, meta in zip(res["documents"][0], res["metadatas"][0])
    ]


def search(query: str, k: int = 3):
    kw = _keyword_search(query, k)
    vec = _vector_search(query, k)

    # dono ke results baari-baari se milao, duplicate hata do
    merged, seen = [], set()
    for pair in zip(kw + [None] * k, vec + [None] * k):
        for d in pair:
            if d and d["name"] not in seen:
                seen.add(d["name"])
                merged.append(d)
    return merged[: k + 2]