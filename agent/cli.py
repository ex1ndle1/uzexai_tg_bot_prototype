"""CLI для проверки агента без Telegram.

    python -m agent.cli "Сколько сделок по хлопку в 2024?"
    python -m agent.cli            # интерактивный режим
"""

from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from .orchestrator import Agent  # noqa: E402


async def main() -> None:
    agent = Agent()
    history: list[dict[str, str]] = []

    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        print(await agent.answer(question, history))
        return

    print("UZEX agent (пустая строка — выход). Пример: Сколько сделок по хлопку в 2024?")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        answer = await agent.answer(question, history)
        print("\n" + answer)
        history += [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ]


if __name__ == "__main__":
    asyncio.run(main())
