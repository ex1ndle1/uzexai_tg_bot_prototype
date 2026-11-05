"""Contract tests for the raw UZEX registry.

Every assertion in this file comes from one of the two README files:

* ``README.md`` (task description and stated dataset size)
* ``uzex_spot_data/README.md`` (column semantics, formulas, state meanings)

No solution code is imported or inspected.  The tests talk directly to the CSV
files through :mod:`tests_independent.reference`.
"""

from __future__ import annotations

import unittest

from tests_independent import reference as ref


class RegistryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ref.connection()

    # ------------------------------------------------------------------ size
    def test_deal_count_and_yearly_distribution_match_readme(self) -> None:
        expected = {
            "2022": 84299,
            "2023": 93985,
            "2024": 106647,
            "2025": 115763,
            "2026": 99306,
        }
        self.assertEqual(ref.scalar("SELECT count(*) FROM deals"), 500000)
        actual = dict(ref.query("SELECT year(deal_date), count(*) FROM deals GROUP BY 1"))
        self.assertEqual({str(k): v for k, v in actual.items()}, expected)
        self.assertEqual(sum(expected.values()), 500000)

    def test_registry_entities_match_readme(self) -> None:
        self.assertEqual(ref.scalar("SELECT count(*) FROM contracts"), 6616)
        self.assertEqual(ref.scalar("SELECT count(*) FROM participants"), 14392)
        self.assertEqual(ref.scalar("SELECT count(*) FROM brokers"), 120)

    def test_deal_date_range_matches_readme(self) -> None:
        start, end = ref.one("SELECT min(deal_date), max(deal_date) FROM deals")
        self.assertEqual(start.isoformat(), "2022-01-03")
        self.assertEqual(end.isoformat(), "2026-10-30")

    def test_usd_rates_cover_every_calendar_day(self) -> None:
        count, distinct, start, end, span = ref.one(
            "SELECT count(*), count(DISTINCT rate_date), min(rate_date), "
            "max(rate_date), datediff('day', min(rate_date), max(rate_date)) + 1 "
            "FROM rates"
        )
        self.assertEqual(count, 1765)
        self.assertEqual(distinct, count)
        self.assertEqual(span, count)
        self.assertEqual(start.isoformat(), "2022-01-01")
        self.assertEqual(end.isoformat(), "2026-10-31")
        self.assertEqual(ref.scalar("SELECT count(*) FROM rates WHERE rate IS NULL OR rate <= 0"), 0)

    def test_primary_keys_are_unique(self) -> None:
        self.assertEqual(ref.scalar("SELECT count(*) - count(DISTINCT deal_no) FROM deals"), 0)
        self.assertEqual(ref.scalar("SELECT count(*) - count(DISTINCT contract_no) FROM contracts"), 0)
        self.assertEqual(ref.scalar("SELECT count(*) - count(DISTINCT inn) FROM participants"), 0)
        self.assertEqual(ref.scalar("SELECT count(*) - count(DISTINCT code) FROM brokers"), 0)

    # -------------------------------------------------------------- semantics
    def test_status_domain_is_the_documented_set(self) -> None:
        present = {row[0] for row in ref.query("SELECT DISTINCT status FROM deals")}
        self.assertEqual(present, set(ref.STATES))

    def test_new_status_exists_only_on_the_last_export_day(self) -> None:
        wrong = ref.scalar(
            "SELECT count(*) FROM deals "
            "WHERE status = 'Новый' AND deal_date <> DATE '2026-10-30'"
        )
        self.assertEqual(wrong, 0)
        self.assertEqual(
            ref.scalar("SELECT count(*) FROM deals WHERE status = 'Новый'"), 964
        )
        self.assertEqual(
            ref.scalar("SELECT count(*) FROM deals WHERE deal_date = DATE '2026-10-30'"),
            964,
        )

    def test_delivered_column_follows_status_meaning(self) -> None:
        # «Исполнен» means fully delivered.
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals "
                "WHERE status = 'Исполнен' AND (delivered IS NULL OR delivered <> qty)"
            ),
            0,
        )
        # «Исполнен(Частично)» means a part was delivered, not everything.
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals WHERE status = 'Исполнен(Частично)' "
                "AND NOT (delivered > 0 AND delivered < qty)"
            ),
            0,
        )
        # All other states have nothing delivered yet.
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals WHERE status NOT IN "
                "('Исполнен', 'Исполнен(Частично)') AND delivered IS NOT NULL"
            ),
            0,
        )

    def test_payment_and_delivery_dates_follow_status_meaning(self) -> None:
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals WHERE status IN "
                "('Новый', 'Не подписан', 'Заключен', 'Ждёт оплату', "
                "'Аннулирован(Не оплачен)') AND (pay_date IS NOT NULL OR delivery_date IS NOT NULL)"
            ),
            0,
        )
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals WHERE status = 'Оплачен' "
                "AND (pay_date IS NULL OR delivery_date IS NOT NULL)"
            ),
            0,
        )
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals WHERE status IN "
                "('Исполнен', 'Исполнен(Частично)') "
                "AND (pay_date IS NULL OR delivery_date IS NULL)"
            ),
            0,
        )

    def test_deal_numbers_are_unique_and_grow_with_time(self) -> None:
        self.assertEqual(ref.scalar("SELECT count(*) - count(DISTINCT deal_no) FROM deals"), 0)
        self.assertEqual(
            ref.scalar(
                """
                SELECT count(*) FROM (
                    SELECT deal_date, deal_time,
                           lag(deal_date) OVER (ORDER BY deal_no) AS prev_date,
                           lag(deal_time) OVER (ORDER BY deal_no) AS prev_time
                    FROM deals
                )
                WHERE prev_date IS NOT NULL
                  AND (deal_date < prev_date
                       OR (deal_date = prev_date AND deal_time < prev_time))
                """
            ),
            0,
        )

    # ---------------------------------------------------------- relationships
    def test_referential_integrity(self) -> None:
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals d LEFT JOIN contracts c USING (contract_no) "
                "WHERE c.contract_no IS NULL"
            ),
            0,
        )
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals d LEFT JOIN participants p "
                "ON p.inn = d.seller_inn WHERE p.inn IS NULL"
            ),
            0,
        )
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals d LEFT JOIN participants p "
                "ON p.inn = d.buyer_inn WHERE p.inn IS NULL"
            ),
            0,
        )
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals d LEFT JOIN brokers b "
                "ON b.code = d.seller_broker WHERE b.code IS NULL"
            ),
            0,
        )
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals d LEFT JOIN brokers b "
                "ON b.code = d.buyer_broker WHERE b.code IS NULL"
            ),
            0,
        )

    def test_contract_card_agrees_with_deals(self) -> None:
        self.assertEqual(
            ref.scalar(
                """
                SELECT count(*) FROM deals d JOIN contracts c USING (contract_no)
                WHERE d.contract_type <> c.contract_type
                   OR d.contract_form <> c.contract_form
                   OR d.unit <> c.unit
                   OR abs(d.lot - c.lot) > 1e-9
                   OR d.seller_inn <> c.seller_inn
                """
            ),
            0,
        )
        # A deal can only be struck while its contract card is valid.
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals d JOIN contracts c USING (contract_no) "
                "WHERE d.deal_date < c.reg_date OR d.deal_date > c.valid_until"
            ),
            0,
        )

    def test_export_is_usd_and_internal_is_uzs(self) -> None:
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals_enriched "
                "WHERE contract_type = 'Экспортный' AND currency <> 'Доллар США'"
            ),
            0,
        )
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals_enriched "
                "WHERE contract_type = 'Внутренний' AND currency <> 'Узбекский сум'"
            ),
            0,
        )
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals_enriched WHERE contract_type = 'Экспортный'"
            ),
            6590,
        )

    def test_participant_and_broker_formats(self) -> None:
        self.assertEqual(
            ref.scalar("SELECT count(*) FROM participants WHERE NOT regexp_matches(inn, '^[0-9]{9}$')"),
            0,
        )
        self.assertEqual(
            ref.scalar("SELECT count(*) FROM brokers WHERE NOT regexp_matches(code, '^BK[0-9]{3}$')"),
            0,
        )
        self.assertEqual(
            {row[0] for row in ref.query("SELECT DISTINCT role FROM participants")},
            {"Продавец", "Покупатель"},
        )
        self.assertEqual(
            {row[0] for row in ref.query("SELECT DISTINCT currency FROM contracts")},
            {"Узбекский сум", "Доллар США"},
        )

    # --------------------------------------------------------------- formulas
    def test_core_numeric_fields_are_present_and_positive(self) -> None:
        bad = ref.scalar(
            "SELECT count(*) FROM deals WHERE qty IS NULL OR lot IS NULL "
            "OR amount IS NULL OR start_price IS NULL "
            "OR qty <= 0 OR lot <= 0 OR amount <= 0 OR start_price <= 0"
        )
        self.assertEqual(bad, 0)

    def test_volume_and_unit_price_formulas(self) -> None:
        # Price per unit = amount / (quantity x lot), README.
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals_enriched "
                "WHERE abs(unit_price * volume - amount) "
                "      > greatest(1e-6 * abs(amount), 0.01)"
            ),
            0,
        )
        self.assertEqual(
            ref.scalar("SELECT count(*) FROM deals_enriched WHERE volume IS NULL"), 0
        )
        # Volume is quantity x lot in the contract's unit.
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals_enriched WHERE abs(volume - qty * lot) > 1e-9"
            ),
            0,
        )

    def test_amount_uzs_conversion(self) -> None:
        # Internal deals stay in UZS.
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals_enriched WHERE currency = 'Узбекский сум' "
                "AND abs(amount_uzs - amount) > greatest(1e-6 * abs(amount), 0.01)"
            ),
            0,
        )
        # Export deals are converted with the rate on the deal date only.
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals_enriched WHERE currency = 'Доллар США' "
                "AND abs(amount_uzs - amount * usd_rate) "
                "    > greatest(1e-6 * abs(amount_uzs), 0.01)"
            ),
            0,
        )
        self.assertEqual(
            ref.scalar(
                "SELECT count(*) FROM deals_enriched "
                "WHERE contract_type = 'Экспортный' AND usd_rate IS NULL"
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
