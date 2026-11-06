"""Telegram-бот UZEX: RU + UZ, с кнопкой «🔍 SQL» под ответом."""

from __future__ import annotations

import asyncio
import html
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, F, Router  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.filters import Command, CommandStart  # noqa: E402
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("uzex.bot")


def build_agent():
    """На Render — тонкий клиент к API на YC; локально — агент целиком."""
    api_url = os.getenv("AGENT_API_URL", "").strip()
    if api_url:
        from agent.client import RemoteAgent  # noqa: PLC0415

        log.info("Режим бота: удалённый API %s", api_url)
        return RemoteAgent()
    from agent.orchestrator import Agent  # noqa: PLC0415

    log.info("Режим бота: локальный агент (DuckDB + LLM)")
    return Agent()


router = Router()
agent = build_agent()
HISTORY: dict[int, list[dict[str, str]]] = {}
MAX_HISTORY = 8
HISTORY_CHARS = 4000  # лимит элемента истории на стороне API (agent/api.py)

# Трейсы SQL по каждому ответу: chat_id -> {message_id -> {"lang": str, "trace": list}}
TRACES: dict[int, dict[int, dict]] = {}
MAX_TRACES_PER_CHAT = 10

WELCOME = (
    "👋 UZEX bitimlar reyestri agenti\n\n"
    "Bitimlar, shartnomalar, kompaniyalar, narxlar va dollar kursi haqida "
    "rus yoki o‘zbek tilida so‘rang.\n\n"
    "👋 Агент по реестру сделок UZEX\n\n"
    "Спрашивайте о сделках, контрактах, компаниях, ценах и курсе доллара — "
    "на русском или узбекском.\n\n"
    "Ma’lumotlar reyestrdan olinadi; javob bo‘lmasa, agent buni ochiq aytadi.\n"
    "Ответы строятся только по данным реестра; если данных нет — агент так и скажет."
)

EXAMPLES = [
    ("🇺🇿 2024-yilda paxta bo‘yicha top-5 sotuvchi", "2024-yilda paxta bo‘yicha eng katta sotuvchilar top-5"),
    ("🇺🇿 Dollar kursi 2025-03-15", "2025-03-15 sanasidagi dollar kursi qancha?"),
    ("🇷🇺 Топ-5 товаров по сумме сделок в 2024", "Топ-5 товаров по сумме сделок в 2024 году"),
    ("🇷🇺 Сделка №100001", "Покажи сделку №100001"),
    ("🇷🇺 Пшеница: цены и объёмы 2025", "Сколько пшеницы продали в 2025 и по какой средней цене?"),
    ("🇷🇺 Компания ИНН 921912462", "Что за компания с ИНН 921912462?"),
]


def examples_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"q:{index}")]
        for index, (label, _question) in enumerate(EXAMPLES)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def split_message(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit:
            parts.append(current)
            current = ""
        current += line
    if current:
        parts.append(current)
    return parts


# ---------------------------------------------------------------------------
# SQL-трейс: кнопка под ответом + форматирование по кнопке
# ---------------------------------------------------------------------------

def _short(value, limit: int = 200) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def remember_trace(chat_id: int, message_id: int, lang: str, trace: list) -> None:
    store = TRACES.setdefault(chat_id, {})
    store[message_id] = {"lang": lang, "trace": trace}
    if len(store) > MAX_TRACES_PER_CHAT:
        for key in sorted(store)[:-MAX_TRACES_PER_CHAT]:
            store.pop(key, None)


def sql_keyboard(trace: list) -> InlineKeyboardMarkup | None:
    """Кнопка «🔍 SQL (N)» — только если SQL действительно выполнялся."""
    executed = [q for q in trace if not q.get("cached")]
    if not executed:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=f"🔍 SQL ({len(executed)})",
                callback_data="sql",
            )
        ]],
    )


def format_sql_trace(lang: str, trace: list) -> str:
    executed = [q for q in trace if not q.get("cached")]
    if lang == "uz":
        lines = [f"🛠 <b>SQL so‘rovlari ({len(executed)})</b>"]
        rows_word, ms_word, args_word = "qator", "vaqt", "argumentlar"
        cached_note = "⚠️ Javob keshdan — SQL bajarilmadi."
        missing_note = f"… yana {len(executed) - 12} so‘rov"
    else:
        lines = [f"🛠 <b>SQL-запросы ({len(executed)})</b>"]
        rows_word, ms_word, args_word = "строк", "мс", "аргументы"
        cached_note = "⚠️ Ответ из кэша — SQL не выполнялся."
        missing_note = f"… ещё {len(executed) - 12} запрос(ов)"

    if any(q.get("cached") for q in trace):
        lines.append(f"<i>{cached_note}</i>")

    for index, entry in enumerate(executed[:12], 1):
        tool = (entry.get("tool") or "sql").strip() or "sql"
        sql = html.escape(_short(entry.get("sql") or "", 900))
        lines.append(f"<b>{index}. 🔧 {html.escape(tool)}</b>")
        lines.append(f"<code>{sql}</code>")
        params = entry.get("params")
        if params:
            lines.append(f"· {args_word}: <code>{html.escape(_short(params, 300))}</code>")
        if entry.get("error"):
            lines.append(f"❌ {html.escape(_short(entry['error'], 250))}")
        else:
            lines.append(f"→ {rows_word}: {entry.get('rows')} · {ms_word}: {entry.get('ms')}")
    if len(executed) > 12:
        lines.append(missing_note)
    return "\n".join(lines)


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    await message.answer(WELCOME, reply_markup=examples_keyboard())


@router.message(Command("help"))
async def on_help(message: Message) -> None:
    await message.answer(WELCOME, reply_markup=examples_keyboard())


@router.message(Command("examples"))
async def on_examples(message: Message) -> None:
    await message.answer("Misollar / Примеры:", reply_markup=examples_keyboard())


async def _reply(message: Message, question: str) -> None:
    chat_id = message.chat.id
    await message.bot.send_chat_action(chat_id, "typing")
    history = HISTORY.get(chat_id, [])
    try:
        answer = await agent.answer(question, history)
    except Exception:
        log.exception("agent failure")
        answer = ("Kechirasiz, xatolik yuz berdi. Qayta urinib ko‘ring.\n"
                  "Извините, произошла ошибка. Попробуйте ещё раз.")

    HISTORY[chat_id] = (history + [
        {"role": "user", "content": question[:HISTORY_CHARS]},
        {"role": "assistant", "content": answer[:HISTORY_CHARS]},
    ])[-MAX_HISTORY:]

    trace = getattr(agent, "last_queries", []) or []
    try:
        from agent.orchestrator import detect_lang  # noqa: PLC0415
        lang = detect_lang(question)
    except Exception:  # noqa: BLE001 — без duckdb (remote-режим) просто ru
        lang = "ru"
    chunks = split_message(answer)
    for index, chunk in enumerate(chunks):
        kwargs = {}
        if trace and index == len(chunks) - 1:
            markup = sql_keyboard(trace)
            if markup:
                kwargs["reply_markup"] = markup
        sent = await message.answer(chunk, **kwargs)
        if trace and index == len(chunks) - 1 and sent:
            remember_trace(chat_id, sent.message_id, lang, trace)


@router.callback_query(F.data == "sql")
async def on_sql_trace(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.message:
        return
    chat_id = callback.message.chat.id
    message_id = callback.message.message_id
    item = TRACES.get(chat_id, {}).get(message_id)
    if not item:
        await callback.message.answer(
            "🛠 Трейс недоступен: хранятся последние 10 ответов чата.\n"
            "🛠 Trace mavjud emas: so‘nggi 10 ta javob saqlanadi."
        )
        return
    text = format_sql_trace(item["lang"], item["trace"])
    for chunk in split_message(text):
        await callback.message.answer(chunk, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("q:"))
async def on_example(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        question = EXAMPLES[int(callback.data[2:])][1]
    except (ValueError, IndexError):
        return
    if callback.message:
        await _reply(callback.message, question)


@router.message(F.text)
async def on_text(message: Message) -> None:
    await _reply(message, message.text or "")


async def main() -> None:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не задан. Скопируйте .env.example в .env и вставьте токен BotFather.")
    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(router)
    log.info("Бот запущен (polling)")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
