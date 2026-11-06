"""HTTP-ядро агента (Yandex Cloud VM): FastAPI поверх orchestrator.Agent.

Запуск:
    uvicorn agent.api:app --host 0.0.0.0 --port 8000

Переменные окружения:
    AGENT_API_TOKEN — общий секрет с ботом; если пусто, API открыт (только для dev).
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

load_dotenv()

from . import db  # noqa: E402
from .orchestrator import Agent  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("uzex.api")

API_TOKEN = os.getenv("AGENT_API_TOKEN", "").strip()
MAX_HISTORY = 12
MAX_QUESTION = 4000
MAX_HISTORY_ITEM = 12_000

app = FastAPI(title="UZEX Deal Agent API", version="1.0.0", docs_url="/docs", redoc_url=None)
agent = Agent()


class HistoryItem(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=MAX_HISTORY_ITEM)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    history: list[HistoryItem] = Field(default_factory=list)
    chat_id: int | None = None

    @field_validator("history")
    @classmethod
    def limit_history(cls, value: list[HistoryItem]) -> list[HistoryItem]:
        return value[-MAX_HISTORY:]


class AskResponse(BaseModel):
    answer: str
    queries: list[dict[str, Any]] = Field(default_factory=list)


def require_token(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if not API_TOKEN:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, API_TOKEN):
        raise HTTPException(status_code=401, detail="invalid api key")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    try:
        db.fetchone("SELECT 1 AS ok FROM deals LIMIT 1")
        database = "ok"
    except Exception as exc:  # noqa: BLE001
        log.error("healthz db error: %s", exc)
        database = "error"
    return {"status": "ok" if database == "ok" else "degraded", "database": database}


@app.post("/api/ask", response_model=AskResponse, dependencies=[Depends(require_token)])
async def ask(payload: AskRequest) -> AskResponse:
    history = [item.model_dump() for item in payload.history]
    log.info("ask chat=%s q=%r", payload.chat_id, payload.question[:120])
    answer = await agent.answer(payload.question, history)
    queries = getattr(agent, "last_queries", [])
    log.info("done chat=%s sql_calls=%s", payload.chat_id, len(queries))
    return AskResponse(answer=answer, queries=queries)


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal error"})


INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UZEX — реестр биржевых сделок</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, -apple-system, Segoe UI, sans-serif; max-width: 820px;
           margin: 0 auto; padding: 24px; line-height: 1.5; }
    h1 { font-size: 22px; margin-bottom: 4px; }
    .sub { opacity: .7; margin-bottom: 18px; }
    textarea { width: 100%; min-height: 76px; padding: 10px; font: inherit; box-sizing: border-box; }
    button { margin-top: 10px; padding: 10px 22px; font: inherit; cursor: pointer; }
    #answer { white-space: pre-wrap; margin-top: 18px; padding: 14px; border-radius: 10px;
              background: rgba(127,127,127,.12); min-height: 24px; }
    .hint { opacity: .6; font-size: 13px; margin-top: 8px; }
    code { font-size: 13px; }
  </style>
</head>
<body>
  <h1>UZEX — AI-агент по реестру сделок</h1>
  <div class="sub">UZ / RU · 500 000 сделок, 2022–2026 · ответы только по данным реестра</div>
  <textarea id="q" placeholder="Например: 2024-yilda paxta bo‘yicha eng katta sotuvchi kim?"></textarea>
  <button id="send">Спросить / So‘rash</button>
  <div class="hint">Если в API задан AGENT_API_TOKEN, укажите его в поле ниже.</div>
  <input id="key" placeholder="X-API-Key (если нужен)" style="width:100%;padding:8px;margin-top:8px">
  <div id="answer"></div>
  <script>
    const q = document.getElementById('q'), out = document.getElementById('answer');
    document.getElementById('send').onclick = async () => {
      const question = q.value.trim();
      if (!question) return;
      out.textContent = '…';
      try {
        const headers = {'Content-Type': 'application/json'};
        const key = document.getElementById('key').value.trim();
        if (key) headers['X-API-Key'] = key;
        const r = await fetch('/api/ask', {method: 'POST', headers,
          body: JSON.stringify({question, history: []})});
        const data = await r.json();
        out.textContent = data.answer || data.detail || JSON.stringify(data);
      } catch (e) { out.textContent = 'Ошибка сети: ' + e; }
    };
    q.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) document.getElementById('send').click(); });
  </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    return INDEX_HTML


if not API_TOKEN:
    log.warning("AGENT_API_TOKEN не задан — API работает без авторизации (только для разработки)")
