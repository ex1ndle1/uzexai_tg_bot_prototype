"""Проверка HTTP-ядра агента без LLM (offline-режим).

Запуск:
    .venv/bin/python -m tests.test_api
или:
    pytest tests/test_api.py -q
"""

from __future__ import annotations

import os

# Важно: выставляем до импорта agent.api, иначе load_dotenv подхватит .env.
os.environ["AGENT_API_TOKEN"] = "test-token"
os.environ["LLM_API_KEY"] = ""
os.environ["OPENROUTER_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from agent.api import app  # noqa: E402

client = TestClient(app)
AUTH = {"X-API-Key": "test-token"}


def test_healthz() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok", body
    assert body["database"] == "ok", body


def test_ask_requires_token() -> None:
    assert client.post("/api/ask", json={"question": "тест"}).status_code == 401
    bad = client.post("/api/ask", json={"question": "тест"}, headers={"X-API-Key": "nope"})
    assert bad.status_code == 401


def test_ask_deal_offline() -> None:
    response = client.post(
        "/api/ask",
        json={"question": "Покажи сделку №100001", "chat_id": 1},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    answer = response.json()["answer"]
    assert "100001" in answer
    assert "Шрот" in answer


def test_ask_validates_empty_question() -> None:
    response = client.post("/api/ask", json={"question": ""}, headers=AUTH)
    assert response.status_code == 422


def test_index_page() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "UZEX" in response.text


def test_ask_includes_sql_trace() -> None:
    response = client.post(
        "/api/ask",
        json={"question": "Что за компания с ИНН 921912462?", "chat_id": 2},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    queries = response.json().get("queries")
    assert isinstance(queries, list), queries
    assert queries, "ожидали непустой SQL-трейс"
    assert any(q.get("tool") == "get_company" or "participants" in q.get("sql", "") for q in queries), queries


def main() -> None:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failed += 1
            print(f"❌ {test.__name__}: {exc}")
        else:
            print(f"✅ {test.__name__}")
    print(f"\nAPI: {len(tests) - failed}/{len(tests)}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
