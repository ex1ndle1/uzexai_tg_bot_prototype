"""Проверка качества: инструменты против эталонного SQL + прогон вопросов через LLM.

    python -m tests.eval            # инструменты всегда, LLM — если задан ключ
    python -m tests.eval --tools    # только инструменты (без сети)
    python -m tests.eval --api http://<IP>:8000 --token <secret>
                                    # вопросы через удалённое ядро на YC
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

from dotenv import load_dotenv

load_dotenv()

from agent import db  # noqa: E402
from agent.tools import call_tool  # noqa: E402


def _num(sql: str):
    row = db.fetchone(sql)
    return next(iter(row.values())) if row else None


# (описание, инструмент, аргументы, SQL-эталон, как достать число из результата)
TOOL_CASES = [
    (
        "всего сделок",
        "dataset_overview",
        {},
        "SELECT count(*) FROM deals",
        lambda r: r["totals"]["deals"],
    ),
    (
        "сделок за 2024",
        "aggregate_deals",
        {"group_by": "year", "metric": "deals", "date_from": "2024", "date_to": "2024"},
        "SELECT count(*) FROM deals WHERE year(deal_date) = 2024",
        lambda r: r["summary"]["deals"],
    ),
    (
        "сумма сделок 2024, сум",
        "aggregate_deals",
        {"group_by": "year", "metric": "amount_uzs", "date_from": "2024", "date_to": "2024"},
        "SELECT sum(amount_uzs) FROM deals_enriched WHERE year(deal_date) = 2024",
        lambda r: r["summary"]["amount_uzs"],
    ),
    (
        "объём хлопка 2025",
        "aggregate_deals",
        {"group_by": "year", "metric": "volume", "product": "хлопок", "date_from": "2025", "date_to": "2025"},
        """SELECT sum(volume) FROM deals_enriched
           WHERE year(deal_date) = 2025
             AND (product_key LIKE '%хлопок%' OR product_name ILIKE '%хлопок%')""",
        lambda r: r["summary"]["volume"],
    ),
    (
        "топ-товар 2024 по сумме",
        "aggregate_deals",
        {"group_by": "product", "metric": "amount_uzs", "date_from": "2024", "date_to": "2024", "limit": 1},
        """SELECT coalesce(product_canon, contract_product_name, product_name) FROM deals_enriched
           WHERE year(deal_date) = 2024
           GROUP BY 1 ORDER BY sum(amount_uzs) DESC LIMIT 1""",
        lambda r: r["rows"][0]["label"],
    ),
    (
        "сделок пшеницы 2025",
        "product_stats",
        {"product": "пшеница", "date_from": "2025", "date_to": "2025"},
        """SELECT count(*) FROM deals_enriched
           WHERE year(deal_date) = 2025 AND product_key LIKE '%пшеница%'""",
        lambda r: r["totals"]["deals"],
    ),
    (
        "сделок компании 921912462 как продавца",
        "company_stats",
        {"inn": "921912462"},
        "SELECT count(*) FROM deals WHERE seller_inn = '921912462'",
        lambda r: r["as_seller"]["totals"]["deals"],
    ),
    (
        "курс на 2025-03-15",
        "usd_rate",
        {"date_from": "2025-03-15"},
        "SELECT rate FROM usd_rates WHERE rate_date = DATE '2025-03-15'",
        lambda r: r["rate"],
    ),
    (
        "сделки со статусом Аннулирован",
        "aggregate_deals",
        {"group_by": "status", "metric": "deals", "status": "Аннулирован"},
        "SELECT count(*) FROM deals WHERE status LIKE '%Аннулирован%'",
        lambda r: r["summary"]["deals"],
    ),
    (
        "экспортных сделок",
        "aggregate_deals",
        {"group_by": "contract_type", "metric": "deals", "contract_type": "Экспортный"},
        "SELECT count(*) FROM deals WHERE contract_type = 'Экспортный'",
        lambda r: r["summary"]["deals"],
    ),
    (
        "компания по кириллическому названию (в реестре латиница)",
        "get_company",
        {"query": "РУКУШО ФЕРМЕР"},
        "SELECT inn FROM participants WHERE name ILIKE '%RUKUSHO%'",
        lambda r: r["companies"][0]["inn"],
    ),
    (
        "сделок по контракту 100034",
        "get_contract",
        {"contract_no": 100034},
        "SELECT count(*) FROM deals WHERE contract_no = 100034",
        lambda r: r["deal_stats"]["deals"],
    ),
    (
        "оборот брокера BK001 как брокера продавца",
        "get_broker",
        {"query": "BK001"},
        "SELECT count(*) FROM deals WHERE seller_broker = 'BK001'",
        lambda r: r["brokers"][0]["stats"]["deals_as_seller_broker"],
    ),
]


def run_tool_cases() -> tuple[int, int]:
    passed = 0
    for desc, tool, args, truth_sql, extract in TOOL_CASES:
        try:
            result = call_tool(tool, args)
            got = extract(result)
            expect = _num(truth_sql)
            try:
                ok = abs(float(got) - float(expect)) <= max(1e-6, abs(float(expect)) * 1e-9)
            except (TypeError, ValueError):
                ok = str(got).strip().lower() == str(expect).strip().lower()
        except Exception as exc:  # noqa: BLE001
            got, expect, ok = f"ERROR {exc}", "?", False
        passed += ok
        mark = "✅" if ok else "❌"
        print(f"{mark} {desc}: got={got} expect={expect}")
    return passed, len(TOOL_CASES)


LLM_CASES = [
    (
        "Сколько всего сделок в реестре?",
        ["500 000", "500000"],
        "за период",
        ["Manba"],
    ),
    (
        "Покажи сделку №100001",
        ["100001", "Шрот"],
        "2022",
    ),
    (
        "Что за компания с ИНН 921912462?",
        ["RUKUSHO", "921912462"],
        "Namangan",
    ),
    (
        "2025-03-15 sanasidagi dollar kursi qancha?",
        ["12 589", "12589"],
        "so‘m",
        ["Источник"],
    ),
    (
        "Какая средняя цена пшеницы в 2025?",
        ["пшениц"],
        "сум",
    ),
    (
        "Сколько самолётов продано на бирже?",
        ["нет", "не найдено", "topilmadi", "yo‘q"],
        None,
    ),
    (
        "2024-yilda paxta bo‘yicha eng katta sotuvchi kim?",
        ["sito"],
        "manba",
        ["Источник"],
    ),
    (
        "Pishiriq uni narxi 2025-yilda qancha?",
        ["мука", "5332"],
        "manba",
        ["Источник", "нет данных", "topilmadi"],
    ),
]


def _normalize(text: str) -> str:
    return re.sub(r"[\s,.\u00a0]", "", text.lower())


async def run_llm_cases(agent=None) -> tuple[int, int]:
    if agent is None:
        from agent import llm
        from agent.orchestrator import Agent

        if not llm.enabled():
            print("\nLLM не настроен — пропускаю проверки ответов (нужен OPENROUTER_API_KEY).")
            return 0, 0
        agent = Agent()

    passed = 0
    for case in LLM_CASES:
        question, must, extra = case[0], case[1], case[2]
        forbidden = case[3] if len(case) > 3 else []
        try:
            answer = await agent.answer(question)
        except Exception as exc:  # noqa: BLE001
            answer = f"ERROR {exc}"
        flat = _normalize(answer)
        ok = any(needle in flat for needle in map(_normalize, must))
        if extra:
            ok = ok and _normalize(extra) in flat
        if forbidden:
            ok = ok and not any(_normalize(word) in flat for word in forbidden)
        passed += ok
        print(f"{'✅' if ok else '❌'} {question}\n   -> {answer[:300]}")
    return passed, len(LLM_CASES)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tools", action="store_true", help="только инструменты, без LLM")
    parser.add_argument("--api", metavar="URL", help="прогнать вопросы через удалённое ядро")
    parser.add_argument("--token", default=os.getenv("AGENT_API_TOKEN", ""), help="X-API-Key ядра")
    args = parser.parse_args()

    if args.api:
        from agent.client import RemoteAgent

        agent = RemoteAgent(base_url=args.api, token=args.token)
        print(f"Удалённое ядро: {args.api}")
        p, n = await run_llm_cases(agent)
        print(f"Ответы LLM (remote): {p}/{n}")
        sys.exit(0 if p == n else 1)

    p, n = run_tool_cases()
    print(f"\nИнструменты: {p}/{n}")

    if not args.tools:
        p2, n2 = await run_llm_cases()
        if n2:
            print(f"Ответы LLM: {p2}/{n2}")
            p += p2
            n += n2

    sys.exit(0 if p == n else 1)


if __name__ == "__main__":
    asyncio.run(main())
