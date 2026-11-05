"""Клиент OpenRouter (OpenAI-совместимый) с перебором бесплатных моделей."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
API_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("LLM_API_KEY") or ""
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "90"))
TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))

# Бесплатные модели OpenRouter; можно переопределить через OPENROUTER_MODELS
# (или LLM_MODELS — удобно при переключении на Ollama Cloud).
DEFAULT_MODELS = [
    "deepseek/deepseek-chat-v3.1:free",
    "google/gemma-4-31b-it:free",
    "z-ai/glm-4.5-air:free",
    "qwen/qwen3-235b-a22b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]

_MODELS_ENV = os.getenv("LLM_MODELS") or os.getenv("OPENROUTER_MODELS") or ""
MODELS = [m.strip() for m in _MODELS_ENV.split(",") if m.strip()] or DEFAULT_MODELS

# Gemma 4 (и другие reasoning-модели) могут отдавать блок размышлений.
# В ответ пользователю и в историю диалога он попадать не должен.
_THOUGHT_RE = re.compile(r"<\|channel>thought\s*(.*?)<channel\|>", re.DOTALL)
_THOUGHT_TAGS_RE = re.compile(r"<\|?(?:think|channel|thought)\|?>", re.IGNORECASE)


def clean_content(text: str | None) -> str:
    """Убирает thinking-канал Gemma 4 и служебные токены из ответа."""
    if not text:
        return ""
    text = _THOUGHT_RE.sub("", text)
    text = _THOUGHT_TAGS_RE.sub("", text)
    return text.strip()



class LLMError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(API_KEY)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("LLM_REFERER", "https://hackathon.uzex.uz"),
        "X-Title": os.getenv("LLM_TITLE", "UZEX Deal Agent"),
    }


async def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Возвращает message из choices[0]. Перебирает модели при 429/5xx."""
    if not API_KEY:
        raise LLMError("LLM_API_KEY / OPENROUTER_API_KEY не задан")

    payload: dict[str, Any] = {
        "messages": messages,
        "temperature": TEMPERATURE if temperature is None else temperature,
        "max_tokens": 1200,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    last_error: str = ""
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        for model in MODELS:
            payload["model"] = model
            try:
                resp = await client.post(f"{BASE_URL}/chat/completions", headers=_headers(), json=payload)
            except httpx.HTTPError as exc:
                last_error = f"{model}: {type(exc).__name__}: {exc}"
                continue

            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = f"{model}: HTTP {resp.status_code} {resp.text[:200]}"
                continue
            if resp.status_code >= 400:
                last_error = f"{model}: HTTP {resp.status_code} {resp.text[:300]}"
                continue

            data = resp.json()
            try:
                message = data["choices"][0]["message"]
            except (KeyError, IndexError, json.JSONDecodeError) as exc:
                last_error = f"{model}: некорректный ответ ({exc})"
                continue

            message.setdefault("role", "assistant")
            message["content"] = clean_content(message.get("content"))
            # Ollama/OpenRouter могут вернуть размышления отдельным полем.
            for key in ("reasoning", "reasoning_content", "thinking"):
                message.pop(key, None)
            message["_model"] = model
            return message

    raise LLMError(f"Все модели недоступны. Последняя ошибка: {last_error}")
