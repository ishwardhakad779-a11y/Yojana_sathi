import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from groq import Groq, RateLimitError, APIError

load_dotenv()
logger = logging.getLogger("yojana_sathi.llm")

# Support single key or comma-separated keys for free rotation:
# e.g. GROQ_API_KEYS="gsk_1...,gsk_2..." or GROQ_API_KEY="gsk_1..."
raw_keys = os.getenv("GROQ_API_KEYS") or os.getenv("GROQ_API_KEY", "")
API_KEYS: List[str] = [k.strip() for k in raw_keys.split(",") if k.strip()]

# Active FREE models on Groq:
# Primary: openai/gpt-oss-20b
# Fallbacks: openai/gpt-oss-120b, qwen/qwen3.8-27b
GROQ_MODELS = [
    os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
]

_clients: List[Groq] = [Groq(api_key=k) for k in API_KEYS]
_key_index = 0


def _get_client() -> Optional[Groq]:
    global _key_index
    if not _clients:
        return None
    return _clients[_key_index % len(_clients)]


def _rotate_key():
    global _key_index
    if len(_clients) > 1:
        _key_index = (_key_index + 1) % len(_clients)
        logger.info(f"Rotated to Groq API key #{_key_index + 1} of {len(_clients)}")


def _wait_time(err_msg: str, attempt: int) -> float:
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", err_msg)
    if m:
        mins = int(m.group(1) or 0)
        val = float(m.group(2))
        secs = mins * 60 + (val / 1000 if m.group(3) == "ms" else val)
        return min(secs + 0.5, 30)
    return min(2.0 * (attempt + 1), 10.0)


def generate(
    prompt: str,
    json_mode: bool = False,
    temperature: float = 0.0,
    model: Optional[str] = None,
    max_retries: int = 3,
) -> str:
    """
    Executes completion using 100% free Groq models with key rotation,
    automatic backoff on 429, and model-level fallback (gpt-oss-20b -> llama-3.3-70b).
    Zero cost, no OpenAI or Claude required.
    """
    if not _clients:
        raise RuntimeError("GROQ_API_KEY is not set in environment.")

    models_to_try = [model] if model else GROQ_MODELS
    last_error: Optional[Exception] = None

    for m in models_to_try:
        client = _get_client()
        for attempt in range(max_retries):
            try:
                kwargs: Dict[str, Any] = {
                    "model": m,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}

                resp = client.chat.completions.create(**kwargs)
                return (resp.choices[0].message.content or "").strip()

            except Exception as e:
                last_error = e
                err_str = str(e)
                logger.warning(
                    f"Groq [{m}] attempt {attempt + 1}/{max_retries} failed: {err_str[:120]}"
                )

                # Daily token limit or 429
                if "TPD" in err_str or "tokens per day" in err_str:
                    logger.warning(f"Groq daily limit on key #{_key_index + 1}. Rotating key or fallback model.")
                    _rotate_key()
                    break  # switch model or key

                if attempt < max_retries - 1:
                    wait = _wait_time(err_str, attempt)
                    time.sleep(wait)

    if last_error:
        raise last_error
    raise RuntimeError("Failed to generate response from Groq.")


def generate_json(prompt: str, temperature: float = 0.0, model: Optional[str] = None) -> Dict[str, Any]:
    text = generate(prompt, json_mode=True, temperature=temperature, model=model)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON response: {text[:200]}")
        raise e
