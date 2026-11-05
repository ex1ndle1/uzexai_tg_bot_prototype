"""Клиент API агента для тонкого Telegram-бота на Render."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

log = logging.getLogger("uzex.client")


class AgentAPIError(RuntimeError):
    pass


class RemoteAgent:
    """Тот же интерфейс, что у orchestrator.Agent, но запросы уходят на YC."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
        retries: int = 2,
    ) -> None:
        self.base_url = (base_url or os.getenv("AGENT_API_URL", "")).rstrip("/")
        if not self.base_url:
            raise ValueError("AGENT_API_URL не задан")
        self.token = token if token is not None else os.getenv("AGENT_API_TOKEN", "").strip()
        self.timeout = timeout or float(os.getenv("AGENT_API_TIMEOUT", "150"))
        self.retries = retries

    async def answer(self, question: str, history: list[dict[str, str]] | None = None) -> str:
        payload: dict[str, Any] = {"question": question, "history": history or []}
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-API-Key"] = self.token

        last_error = ""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(1, self.retries + 2):
                try:
                    resp = await client.post(f"{self.base_url}/api/ask", json=payload, headers=headers)
                except httpx.HTTPError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                else:
                    if resp.status_code == 200:
                        return resp.json().get("answer", "").strip()
                    if resp.status_code == 401:
                        raise AgentAPIError("API отклонил ключ (401): проверьте AGENT_API_TOKEN")
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if attempt <= self.retries:
                    log.warning("API попытка %s не удалась (%s), повтор", attempt, last_error)
                    await asyncio.sleep(1.5 * attempt)

        raise AgentAPIError(last_error or "API недоступен")
