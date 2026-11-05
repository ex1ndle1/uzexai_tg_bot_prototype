"""Black-box acceptance tests through the CLI documented in README.md.

The suite treats ``python -m agent.cli "<question>"`` as a public interface.  It
forces the documented offline mode (no LLM key), which makes these checks
deterministic and independent of the model vendor.

Run:

    .venv/bin/python -m unittest tests_independent.test_agent_offline -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

from tests_independent import answers
from tests_independent import reference as ref

ROOT = ref.ROOT
CLI_TIMEOUT = 180


def run_cli(question: str, *, offline: bool, timeout: int = CLI_TIMEOUT) -> str:
    env = os.environ.copy()
    if offline:
        for key in ("LLM_API_KEY", "OPENROUTER_API_KEY", "LLM_MODELS", "LLM_BASE_URL"):
            env[key] = ""
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "agent.cli", question],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"CLI failed for {question!r}\n"
            f"exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
    output = proc.stdout.strip()
    if not output:
        raise AssertionError(f"CLI returned an empty answer for {question!r}")
    return output


class OfflineCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ref.connection()

    def assert_text(self, text: str, needle: str) -> None:
        haystack = answers.normalize(text).lower()
        expected = answers.normalize(needle).lower()
        self.assertIn(expected, haystack, f"{needle!r} not found in answer: {text!r}")

    def test_deal_lookup_for_first_deal_of_each_year(self) -> None:
        for year in (2022, 2023, 2024, 2025, 2026):
            with self.subTest(year=year):
                expected = ref.first_deal_of_year(year)
                self.assertIsNotNone(expected)
                output = run_cli(f"Сделка №{expected['deal_no']}", offline=True)
                self.assert_text(output, str(expected["deal_no"]))
                self.assert_text(output, expected["deal_date"])
                self.assert_text(output, expected["product_name"])
                self.assert_text(output, expected["status"])
                answers.assert_number(
                    self,
                    output,
                    expected["amount"],
                    context=f"amount of deal {expected['deal_no']}",
                )

    def test_export_deal_is_reported_in_usd(self) -> None:
        expected = ref.first_export_deal_of_year(2024)
        self.assertIsNotNone(expected)
        output = run_cli(f"Сделка №{expected['deal_no']}", offline=True)
        self.assert_text(output, expected["product_name"])
        self.assert_text(output, "Доллар США")

    def test_internal_deal_is_reported_in_uzs(self) -> None:
        expected = ref.first_internal_deal_of_year(2024)
        self.assertIsNotNone(expected)
        self.assertEqual(expected["currency"], "Узбекский сум")
        output = run_cli(f"Сделка №{expected['deal_no']}", offline=True)
        self.assert_text(output, "Узбекский сум")

    def test_company_lookup_by_inn(self) -> None:
        expected = ref.company("921912462")
        self.assertIsNotNone(expected)
        output = run_cli("ИНН 921912462", offline=True)
        self.assert_text(output, expected["name"])
        self.assert_text(output, expected["role"])
        self.assert_text(output, expected["region"])

    def test_unknown_inn_is_reported_as_missing(self) -> None:
        output = run_cli("ИНН 000000000", offline=True)
        answers.assert_no_data(self, output)

    def test_unknown_deal_number_is_reported_as_missing(self) -> None:
        output = run_cli("Сделка №999999", offline=True)
        answers.assert_no_data(self, output)
        self.assertNotIn("ИНН", output)

    @unittest.expectedFailure
    def test_nine_digit_unknown_deal_is_not_answered_as_inn(self) -> None:
        # Offline parser regressed: a nine-digit deal number is treated as an
        # INN even though the question explicitly says "Сделка №...".
        output = run_cli("Сделка №999999999", offline=True)
        answers.assert_no_data(self, output)
        self.assertNotIn("ИНН", output)

    def test_usd_rate_iso_date(self) -> None:
        expected = ref.rate_on("2025-03-15")
        output = run_cli("Курс USD на 2025-03-15", offline=True)
        answers.assert_number(self, output, expected, context="USD rate")
        self.assert_text(output, "2025-03-15")

    def test_usd_rate_dotted_date(self) -> None:
        expected = ref.rate_on("15.03.2025")
        output = run_cli("курс на 15.03.2025", offline=True)
        answers.assert_number(self, output, expected, context="USD rate")

    def test_uzbek_rate_question(self) -> None:
        expected = ref.rate_on("2025-03-15")
        output = run_cli("2025-03-15 sanasidagi dollar kursi qancha?", offline=True)
        answers.assert_number(self, output, expected, context="UZS/USD rate")

    def test_registry_overview_mentions_size_and_period(self) -> None:
        output = run_cli("Что ты умеешь?", offline=True)
        answers.assert_number(self, output, 500000, context="total deal count")
        self.assert_text(output, "2022-01-03")
        self.assert_text(output, "2026-10-30")


if __name__ == "__main__":
    unittest.main(verbosity=2)
