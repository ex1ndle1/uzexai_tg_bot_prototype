"""Оркестратор: LLM + инструменты + честный fallback без ключа."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import db, llm
from .prompts import RU_LOCK, SYSTEM_PROMPT, UZ_LOCK
from .tools import TOOL_SPECS, call_tool

log = logging.getLogger("uzex.agent")

MAX_STEPS = 5

UZ_WORDS = {
    "qancha", "qaysi", "kim", "qachon", "qayerda", "nechta", "qanday", "bormi",
    "yoq", "yo‘q", "haqida", "boyicha", "bo‘yicha", "yilda", "yil", "oyda", "oy",
    "kunda", "kun", "uchun", "bilan", "orasida", "eng", "katta", "kichik",
    "jami", "hammasi", "bitim", "bitimlar", "shartnoma", "kompaniya", "narx",
    "narxi", "summa", "kurs", "dollar", "sotuvchi", "xaridor", "mahsulot",
    "tovar", "paxta", "bugdoy", "un", "sholi", "shakar", "tuz", "mis",
    "sotilgan", "sotgan", "olingan", "tuzilgan", "bering", "korsating",
}


def detect_lang(text: str) -> str:
    """'uz' для узбекского текста, иначе 'ru'.

    Опираемся на узбекские служебные слова и специфические апострофы,
    а не только на алфавит: в русском вопросе может быть латинское
    название компании («Кто покупал у RUKUSHO‘ FERMER?»).
    """
    low = (text or "").lower()
    tokens = set(re.findall(r"[a-zʻ‘’']+", low))
    if tokens & UZ_WORDS:
        return "uz"
    has_cyrillic = bool(re.search(r"[а-яё]", low))
    if has_cyrillic:
        return "ru"
    if re.search(r"[ʻ‘’]|o'|g'", low) and len(tokens) >= 2:
        return "uz"
    return "ru"


def _fmt_num(value: float | int | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        return f"{value:,}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ")


def _offline_answer(question: str) -> str:
    """Ответ без LLM: для демо/аварийного режима — только точные выборки."""
    lang = detect_lang(question)
    text = question.strip()

    deal = re.search(r"\b(\d{6})\b", text)
    if deal:
        result = call_tool("get_deal", {"deal_no": int(deal.group(1))})
        if result.get("found"):
            d = result["deal"]
            if lang == "uz":
                return (
                    f"Bitim №{d['deal_no']} ({d['deal_date']}): "
                    f"{d['product_name']}, {_fmt_num(d['qty'])} {d['unit']}, "
                    f"summa {_fmt_num(d['amount'])} {d['currency']}. "
                    f"Holat: {d['status']}."
                )
            return (
                f"Сделка №{d['deal_no']} от {d['deal_date']}: "
                f"{d['product_name']}, {_fmt_num(d['qty'])} {d['unit']}, "
                f"сумма {_fmt_num(d['amount'])} {d['currency']}. "
                f"Статус: {d['status']}."
            )
        return (
            f"№{deal.group(1)} raqamli bitim reyestrda topilmadi."
            if lang == "uz"
            else f"Сделки с номером {deal.group(1)} в реестре нет."
        )

    inn = re.search(r"\b(\d{9})\b", text)
    if inn:
        result = call_tool("company_stats", {"inn": inn.group(1)})
        if result.get("found"):
            c = result["company"]
            return (
                f"{c['name']} (STIR {c['inn']}, {c['region']}): "
                f"rol — {c['role']}."
                if lang == "uz"
                else f"{c['name']} (ИНН {c['inn']}, {c['region']}): роль — {c['role']}."
            )
        return "Bunday STIR reyestrda topilmadi." if lang == "uz" else "Компании с таким ИНН в реестре нет."

    date = re.search(r"\b(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})\b", text)
    if ("курс" in text.lower() or "kurs" in text.lower()) and date:
        result = call_tool("usd_rate", {"date_from": date.group(1)})
        if result.get("found"):
            return (
                f"{result['date']} holatiga dollar kursi: {_fmt_num(result['rate'])} so‘m."
                if lang == "uz"
                else f"Курс доллара на {result['date']}: {_fmt_num(result['rate'])} сум."
            )

    overview = call_tool("dataset_overview", {})
    totals = overview["totals"]
    if lang == "uz":
        return (
            f"Reyestrda {_fmt_num(totals['deals'])} ta bitim bor "
            f"({totals['date_from']} — {totals['date_to']}). "
            "Savolni aniqroq bering: bitim raqami, STIR, tovar nomi yoki davr."
        )
    return (
        f"В реестре {_fmt_num(totals['deals'])} сделок "
        f"({totals['date_from']} — {totals['date_to']}). "
        "Уточните вопрос: номер сделки, ИНН, название товара или период."
    )


class Agent:
    def __init__(self, max_steps: int = MAX_STEPS) -> None:
        self.max_steps = max_steps
        self._cache: dict[str, str] = {}
        # SQL-трейс последнего ответа — читают API и кнопка «🔍 SQL» в боте.
        self.last_queries: list[dict[str, Any]] = []

    async def answer(self, question: str, history: list[dict[str, str]] | None = None) -> str:
        key = (question or "").strip().lower()
        if not history and key in self._cache:
            self.last_queries = [{
                "tool": "", "sql": "", "params": [], "rows": None,
                "ms": 0.0, "error": None, "cached": True,
            }]
            return self._cache[key]
        buffer: list[dict[str, Any]] = []
        try:
            with db.record_queries(buffer):
                result = await self._answer(question, history)
        finally:
            self.last_queries = buffer
        if not history and len(self._cache) < 500:
            self._cache[key] = result
        return result

    async def _answer(self, question: str, history: list[dict[str, str]] | None = None) -> str:
        question = (question or "").strip()
        if not question:
            return "Задайте вопрос о биржевых сделках."

        if not llm.enabled():
            log.warning("LLM не настроен — используется offline-режим")
            return _offline_answer(question)

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT + "\n\n" + (UZ_LOCK if detect_lang(question) == "uz" else RU_LOCK),
            }
        ]
        if history:
            messages.extend(history[-6:])
        messages.append({"role": "user", "content": question})

        for step in range(self.max_steps):
            try:
                message = await llm.chat(messages, TOOL_SPECS)
            except llm.LLMError as exc:
                log.error("LLM error: %s", exc)
                return _offline_answer(question)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                final = llm.clean_content(message.get("content"))
                return final or _offline_answer(question)

            messages.append(
                {
                    "role": "assistant",
                    "content": llm.clean_content(message.get("content")),
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                log.info("tool %s(%s)", name, args)
                result = call_tool(name, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False, default=str)[:12000],
                    }
                )
            log.info("step %s: %s tool call(s)", step + 1, len(tool_calls))

        # Лимит шагов исчерпан — просим финальный ответ без инструментов.
        messages.append(
            {
                "role": "user",
                "content": "Сформулируй финальный ответ на основе уже полученных данных. "
                           "Если данных не хватает — скажи об этом.",
            }
        )
        try:
            message = await llm.chat(messages, tools=None)
            return llm.clean_content(message.get("content")) or _offline_answer(question)
        except llm.LLMError:
            return _offline_answer(question)
