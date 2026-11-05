"""Единая точка доступа к DuckDB."""

from __future__ import annotations

import datetime as dt
import decimal
import os
import pathlib
from typing import Any

import duckdb

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "uzex.duckdb"
DB_PATH = pathlib.Path(os.getenv("UZEX_DB", str(DEFAULT_DB)))


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
    con = connect()
    try:
        cur = con.execute(sql, params or [])
        columns = [d[0] for d in cur.description]
        return [
            {col: _jsonable(val) for col, val in zip(columns, row)}
            for row in cur.fetchall()
        ]
    finally:
        con.close()


def fetchone(sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
    rows = fetch(sql, params)
    return rows[0] if rows else None
