"""Telegram-бот UZEX: RU + UZ."""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, F, Router  # noqa: E402
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
        return RemoteAgent(base_url=api_url)
    from agent.orchestrator import Agent  # noqa: PLC0415

    log.info("Режим бота: локальный агент (DuckDB + LLM)")
    return Agent()


router = Router()
agent = build_agent()
HISTORY: dict[int, list[dict[str, str]]] = {}
MAX_HISTORY = 8
# API принимает элементы истории до 4000 символов (см. agent/api.py).
HISTORY_CHARS = 4000

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

    for chunk in split_message(answer):
        await message.answer(chunk)


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
