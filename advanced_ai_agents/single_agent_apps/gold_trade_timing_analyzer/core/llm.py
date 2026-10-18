"""
Tiny multi-provider LLM client.

The research agent is the only component that needs a model, and it should not
force a vendor choice on anyone. Six providers are supported through plain HTTP
(no SDK required beyond `requests`):

    openai      https://api.openai.com/v1          (also any OpenAI-compatible URL)
    groq        https://api.groq.com/openai/v1
    openrouter  https://openrouter.ai/api/v1
    anthropic   https://api.anthropic.com/v1
    gemini      https://generativelanguage.googleapis.com/v1beta
    ollama      http://localhost:11434/v1          (local, no key)

If no key is configured the research agent falls back to a deterministic lexicon
scorer, so the app degrades gracefully instead of breaking.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


PROVIDERS: Dict[str, dict] = {
    "openai": {
        "label": "OpenAI",
        "base": "https://api.openai.com/v1",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "o4-mini"],
        "style": "openai",
        "needs_key": True,
    },
    "groq": {
        "label": "Groq",
        "base": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "openai/gpt-oss-120b"],
        "style": "openai",
        "needs_key": True,
    },
    "openrouter": {
        "label": "OpenRouter",
        "base": "https://openrouter.ai/api/v1",
        "models": ["anthropic/claude-sonnet-4.5", "google/gemini-2.5-pro", "openai/gpt-4o-mini",
                   "meta-llama/llama-3.3-70b-instruct"],
        "style": "openai",
        "needs_key": True,
    },
    "anthropic": {
        "label": "Anthropic",
        "base": "https://api.anthropic.com/v1",
        "models": ["claude-sonnet-4-5", "claude-opus-4-1", "claude-3-5-haiku-latest"],
        "style": "anthropic",
        "needs_key": True,
    },
    "gemini": {
        "label": "Google Gemini",
        "base": "https://generativelanguage.googleapis.com/v1beta",
        "models": ["gemini-2.5-flash", "gemini-2.5-pro"],
        "style": "gemini",
        "needs_key": True,
    },
    "ollama": {
        "label": "Ollama (local)",
        "base": "http://localhost:11434/v1",
        "models": ["llama3.1", "qwen2.5", "mistral"],
        "style": "openai",
        "needs_key": False,
    },
}


class LLMError(RuntimeError):
    pass


def complete(
    provider: str,
    model: str,
    api_key: Optional[str],
    system: str,
    user: str,
    max_tokens: int = 1400,
    temperature: float = 0.3,
    base_url: Optional[str] = None,
    timeout: float = 90.0,
) -> str:
    if requests is None:
        raise LLMError("`requests` is not installed.")
    cfg = PROVIDERS.get(provider)
    style = (cfg or {}).get("style", "openai")
    base = base_url or (cfg or {}).get("base", "")
    if not model:
        raise LLMError("No model selected.")

    try:
        if style == "openai":
            url = f"{base.rstrip('/')}/chat/completions"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            payload = {
                "model": model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if r.status_code >= 400:
                raise LLMError(f"{provider} HTTP {r.status_code}: {r.text[:300]}")
            return r.json()["choices"][0]["message"]["content"]

        if style == "anthropic":
            url = f"{base.rstrip('/')}/messages"
            headers = {
                "Content-Type": "application/json",
                "x-api-key": api_key or "",
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "temperature": temperature,
            }
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if r.status_code >= 400:
                raise LLMError(f"anthropic HTTP {r.status_code}: {r.text[:300]}")
            parts = r.json().get("content", [])
            return "".join(p.get("text", "") for p in parts if p.get("type") == "text")

        if style == "gemini":
            url = f"{base.rstrip('/')}/models/{model}:generateContent?key={api_key or ''}"
            payload = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
            }
            r = requests.post(url, headers={"Content-Type": "application/json"},
                              json=payload, timeout=timeout)
            if r.status_code >= 400:
                raise LLMError(f"gemini HTTP {r.status_code}: {r.text[:300]}")
            cands = r.json().get("candidates", [])
            if not cands:
                raise LLMError("gemini returned no candidates")
            return "".join(p.get("text", "") for p in cands[0]["content"]["parts"])

        raise LLMError(f"Unknown provider style: {style}")
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"{provider} request failed: {e}")


def extract_json(text: str) -> Optional[dict]:
    """Best-effort extraction of the first JSON object in a model response."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start: end + 1])
    except Exception:
        # strip fenced code blocks and retry
        cleaned = text.replace("```json", "").replace("```", "").strip()
        s2, e2 = cleaned.find("{"), cleaned.rfind("}")
        try:
            return json.loads(cleaned[s2: e2 + 1])
        except Exception:
            return None
