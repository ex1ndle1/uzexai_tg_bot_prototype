"""Единая точка доступа к DuckDB."""

from __future__ import annotations

import datetime as dt
import decimal
import os
import pathlib
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import duckdb

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "uzex.duckdb"
DB_PATH = pathlib.Path(os.getenv("UZEX_DB", str(DEFAULT_DB)))

# ---------------------------------------------------------------------------
# Регистратор SQL-запросов: на время одного ответа агента буфер включается,
# и каждый запрос через fetch()/fetchone() дописывает в него запись.
# tracе используется кнопкой «🔍 SQL» в боте (прозрачность анти-халюцинации).
# ---------------------------------------------------------------------------

_TRACE: ContextVar[list[dict[str, Any]] | None] = ContextVar("uzex_sql_trace", default=None)
_TOOL: ContextVar[str] = ContextVar("uzex_sql_tool", default="")
MAX_TRACE_ENTRIES = 64
MAX_SQL_LEN = 1000
MAX_PARAM_LEN = 200


@contextmanager
def record_queries(buffer: list[dict[str, Any]]):
    """Включает запись SQL в переданный список на время блока."""
    token = _TRACE.set(buffer)
    try:
        yield buffer
    finally:
        _TRACE.reset(token)


def set_sql_tool(name: str) -> None:
    """Помечает, каким инструментом выполняются текущие SQL-запросы."""
    _TOOL.set(name or "")


def record_manual(tool: str, sql: str = "", error: str | None = None) -> None:
    """Ручная запись (например, «инструмент не найден») без реального SQL."""
    buffer = _TRACE.get()
    if buffer is not None and len(buffer) < MAX_TRACE_ENTRIES:
        buffer.append(
            {
                "tool": tool or "",
                "sql": sql,
                "params": [],
                "rows": None,
                "ms": 0.0,
                "error": error,
                "cached": False,
            }
        )


def _entry(sql: str, params: list[Any] | None, rows: int | None,
           started: float, error: str | None) -> dict[str, Any]:
    safe_params: list[str] = []
    for param in params or []:
        text = repr(param)
        safe_params.append(text if len(text) <= MAX_PARAM_LEN else text[: MAX_PARAM_LEN - 1] + "…")
    formatted_sql = sql if len(sql) <= MAX_SQL_LEN else sql[: MAX_SQL_LEN - 1] + "…"
    return {
        "tool": _TOOL.get(),
        "sql": formatted_sql,
        "params": safe_params,
        "rows": rows,
        "ms": round((time.perf_counter() - started) * 1000, 2),
        "error": error,
        "cached": False,
    }


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"База {DB_PATH} не найдена. Сначалa выполните: python -m agent.etl"
        )
    return duckdb.connect(str(DB_PATH), read_only=read_only)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, dt.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def fetch(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    buffer = _TRACE.get()
    started = time.perf_counter()
    con = connect()
    try:
        cur = con.execute(sql, params or [])
        columns = [d[0] for d in cur.description]
        rows = [
            {col: _jsonable(val) for col, val in zip(columns, row)}
            for row in cur.fetchall()
        ]
    except Exception as exc:  # noqa: BLE001 — ошибка тоже попадает в трейс
        if buffer is not None and len(buffer) < MAX_TRACE_ENTRIES:
            buffer.append(_entry(sql, params, None, started, f"{type(exc).__name__}: {exc}"))
        raise
    finally:
        con.close()
    if buffer is not None and len(buffer) < MAX_TRACE_ENTRIES:
        buffer.append(_entry(sql, params, len(rows), started, None))
    return rows


def fetchone(sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
    rows = fetch(sql, params)
    return rows[0] if rows else None
