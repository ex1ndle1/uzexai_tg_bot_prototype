"""Opt-in end-to-end tests that exercise the LLM + tool-calling loop.

These tests are skipped unless ``UZEX_LLM_TESTS=1`` because they need a working
LLM provider from ``.env`` and make real model calls:

    UZEX_LLM_TESTS=1 .venv/bin/python -m unittest tests_independent.test_agent_llm -v

They stay black-box: only the CLI is used, and every expected value is
recomputed from the raw CSV registry by ``tests_independent.reference``.
"""

from __future__ import annotations

import os
import unittest

from tests_independent import answers
from tests_independent import reference as ref
from tests_independent.test_agent_offline import run_cli

LLM_ENABLED = os.environ.get("UZEX_LLM_TESTS") == "1"


@unittest.skipUnless(
    LLM_ENABLED,
    "set UZEX_LLM_TESTS=1 to run model-dependent end-to-end tests",
)
class LlmAnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ref.connection()
        cls.metrics = ref.compute_metrics()["aggregates"]

    def ask(self, question: str) -> str:
        return run_cli(question, offline=False, timeout=360)

    def assert_text(self, text: str, needle: str) -> None:
        self.assertIn(
            answers.normalize(needle).lower(),
            answers.normalize(text).lower(),
            f"{needle!r} not found in answer: {text!r}",
        )

    # ------------------------------------------------------------ Russian RU
    def test_total_deals(self) -> None:
        out = self.ask("Сколько всего сделок в реестре?")
        answers.assert_number(self, out, 500000, context="total deals")

    def test_deals_2024(self) -> None:
        out = self.ask("Сколько сделок было заключено в 2024 году?")
        answers.assert_number(self, out, self.metrics["deals_2024"], context="deals 2024")

    def test_amount_2024_in_uzs(self) -> None:
        out = self.ask("Какова общая сумма сделок за 2024 год в сумах?")
        answers.assert_number(
            self,
            out,
            self.metrics["amount_uzs_2024"],
            rel_tol=0.02,
            context="2024 amount in UZS",
        )

    def test_wheat_deals_2025(self) -> None:
        out = self.ask("Сколько сделок по пшенице было в 2025 году?")
        answers.assert_number(
            self, out, self.metrics["wheat_2025_deals"], context="wheat deals 2025"
        )

    def test_wheat_average_price_2025(self) -> None:
        out = self.ask("Какая средняя цена пшеницы в 2025 году?")
        answers.assert_number(
            self,
            out,
            self.metrics["wheat_2025_avg_unit_price"],
            rel_tol=1e-3,
            context="wheat avg price 2025",
        )

    def test_annulled_deals(self) -> None:
        out = self.ask("Сколько сделок было аннулировано?")
        answers.assert_number(
            self, out, self.metrics["annulled_deals"], context="annulled deals"
        )

    def test_export_deals(self) -> None:
        out = self.ask("Сколько экспортных сделок в реестре?")
        answers.assert_number(
            self, out, self.metrics["export_deals"], context="export deals"
        )

    def test_deals_by_contract(self) -> None:
        out = self.ask("Сколько сделок заключено по контракту №100034?")
        answers.assert_number(
            self, out, self.metrics["contract_100034_deals"], context="contract 100034"
        )

    def test_deals_by_seller_broker(self) -> None:
        out = self.ask("Сколько сделок, где брокер продавца — BK001?")
        answers.assert_number(
            self,
            out,
            self.metrics["broker_BK001_seller_deals"],
            context="BK001 seller deals",
        )

    def test_top_seller_2024(self) -> None:
        out = self.ask("Кто крупнейший продавец по сумме сделок в 2024 году?")
        expected = self.metrics["top_seller_2024_by_amount_uzs"]
        company = ref.company(expected["inn"])
        self.assertIsNotNone(company)
        self.assert_text(out, expected["inn"])
        self.assert_text(out, company["name"])

    # ----------------------------------------------------------- Uzbek (UZ)
    def test_deals_2024_uzbek(self) -> None:
        out = self.ask("2024-yilda jami nechta bitim tuzilgan?")
        answers.assert_number(self, out, self.metrics["deals_2024"], context="UZ deals 2024")

    def test_wheat_deals_2025_uzbek(self) -> None:
        out = self.ask("2025-yilda bug'doy bo'yicha nechta bitim bor?")
        answers.assert_number(
            self, out, self.metrics["wheat_2025_deals"], context="UZ wheat 2025"
        )

    def test_usd_rate_uzbek(self) -> None:
        out = self.ask("Dollar kursi 2025-03-15 sanasida qancha?")
        answers.assert_number(
            self, out, self.metrics["usd_rate_2025_03_15"], context="UZ USD rate"
        )

    # -------------------------------------------------------------- honesty
    def test_unknown_product_is_refused(self) -> None:
        out = self.ask("Сколько самолётов продано на бирже?")
        answers.assert_no_data(self, out)

    def test_unknown_inn_in_aggregation_is_refused(self) -> None:
        out = self.ask("Сколько сделок у компании с ИНН 000000000?")
        answers.assert_no_data(self, out)

    def test_forecast_outside_registry_is_refused(self) -> None:
        out = self.ask("Какой будет цена хлопка в 2027 году?")
        lowered = answers.normalize(out).lower()
        refusal_markers = (
            "нет данных",
            "не могу",
            "не содержится",
            "вне реестра",
            "за пределами",
            "прогноз",
            "ma'lumot yo'q",
            "topilmadi",
        )
        self.assertTrue(
            any(marker in lowered for marker in refusal_markers),
            f"agent did not refuse a forecast: {out!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
