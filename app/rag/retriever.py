import json
import re
from pathlib import Path

import chromadb
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

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
    """
    Perform vector search with retry logic for transient failures.

    Args:
        query: Search query string
        k: Number of results to return

    Returns:
        List of documents with text, name, and url
    """
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception)
    )
    def _search_with_retry():
        res = _col.query(query_texts=[query], n_results=k)
        return [
            {"text": doc, "name": meta["name"], "url": meta["url"]}
            for doc, meta in zip(res["documents"][0], res["metadatas"][0])
        ]

    try:
        return _search_with_retry()
    except Exception:
        # Fallback to empty results on persistent failure
        return []


def search(query: str, k: int = 3):
    """
    Hybrid search combining keyword matching and vector search.

    Args:
        query: Search query string
        k: Number of results to return

    Returns:
        List of unique documents merged from both search methods
    """
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


def check_health() -> bool:
    """
    Check if ChromaDB vector store is healthy.

    Returns:
        True if collection has documents, False otherwise
    """
    try:
        return _col.count() > 0
    except Exception:
        return False