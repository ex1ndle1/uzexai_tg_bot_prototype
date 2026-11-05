"""Independent ground truth for the UZEX registry.

This module is deliberately written from the two README files and the raw CSV
registry only.  It does not import ``agent``, ``bot`` or the existing ``tests``
package and it does not read the pre-built DuckDB file, so it can be used to
check the solution from the outside.

Rules encoded here (all taken from ``README.md`` / ``uzex_spot_data/README.md``):

* volume          = Количество x Лот
* unit price      = Сумма сделки / (Количество x Лот)
* export amount   = Сумма сделки x курс USD на дату сделки (contract currency USD)
* internal amount = Сумма сделки (contract currency UZS)
* states          = the eight documented states
"""

from __future__ import annotations

import glob
import os
from datetime import date, datetime
from functools import lru_cache

import duckdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "uzex_spot_data")

STATES = (
    "Новый",
    "Не подписан",
    "Заключен",
    "Ждёт оплату",
    "Оплачен",
    "Исполнен",
    "Исполнен(Частично)",
    "Аннулирован(Не оплачен)",
)


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _num(name: str) -> str:
    """CSV number -> DOUBLE. Thousands are separated by a normal space."""
    col = _ident(name)
    return (
        f"TRY_CAST(replace(replace(trim({col}), ' ', ''), chr(160), '') AS DOUBLE)"
    )


def _date(name: str) -> str:
    col = _ident(name)
    return (
        f"CASE WHEN NULLIF(TRIM({col}), '') IS NULL THEN NULL "
        f"ELSE CAST(strptime({col}, '%d.%m.%Y') AS DATE) END"
    )


def _time(name: str) -> str:
    col = _ident(name)
    return f"CAST(strptime({col}, '%H:%M:%S') AS TIME)"


def _csv(path: str) -> str:
    return f"read_csv('{path.replace(chr(39), chr(39) * 2)}', header=true, all_varchar=true)"


@lru_cache(maxsize=1)
def connection() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with typed views over the untouched source CSV files."""
    con = duckdb.connect()
    deal_files = sorted(glob.glob(os.path.join(DATA_DIR, "deals_*.csv")))
    if not deal_files:
        raise RuntimeError(f"no deals_*.csv files in {DATA_DIR}")
    deal_list = "[" + ", ".join(
        "'" + p.replace("'", "''") + "'" for p in deal_files
    ) + "]"

    con.execute(
        f"CREATE OR REPLACE VIEW raw_deals AS "
        f"SELECT * FROM read_csv({deal_list}, header=true, all_varchar=true)"
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW deals AS
        SELECT
            CAST("Номер сделки" AS BIGINT)                   AS deal_no,
            {_date('Дата сделки')}                            AS deal_date,
            {_time('Время сделки')}                           AS deal_time,
            "Тип торгов"                                      AS auction_type,
            {_num('Количество')}                              AS qty,
            {_num('Сумма сделки')}                            AS amount,
            {_num('Стартовая цена')}                          AS start_price,
            CAST("Номер контракта" AS BIGINT)                 AS contract_no,
            "Наименование товара"                             AS product_name,
            {_num('Лот')}                                     AS lot,
            "Единица измерения"                               AS unit,
            "Регион"                                          AS region,
            "Тип контракта"                                   AS contract_type,
            "Форма контракта"                                 AS contract_form,
            "Состояние сделки"                                AS status,
            "ИНН продавца"                                    AS seller_inn,
            "ИНН покупателя"                                  AS buyer_inn,
            "Брокер продавца"                                 AS seller_broker,
            "Брокер покупателя"                               AS buyer_broker,
            {_date('Дата оплаты')}                            AS pay_date,
            {_date('Дата поставки')}                          AS delivery_date,
            {_num('Поставлено')}                              AS delivered
        FROM raw_deals
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW contracts AS
        SELECT
            CAST("Номер контракта" AS BIGINT)                 AS contract_no,
            "Наименование продукта"                           AS product_name,
            "Марка товара"                                    AS brand,
            "Размер товара"                                   AS size,
            "Сорт товара"                                     AS grade,
            "Тип товара"                                      AS kind,
            "Класс товара"                                    AS class_name,
            "Код ТН ВЭД"                                      AS tnved_code,
            "Группа товара"                                   AS product_group,
            "Категория товара"                                AS product_category,
            "Отрасль промышленности"                          AS industry,
            "Единица измерения"                               AS unit,
            {_num('Лот')}                                     AS lot,
            "Валюта"                                          AS currency,
            {_num('Базисная цена')}                           AS base_price,
            {_num('Сумма залога')}                            AS deposit_pct,
            "Базис поставки"                                  AS delivery_basis,
            "Регион"                                          AS region,
            "Адрес склада"                                    AS warehouse_address,
            "Тип торгов"                                      AS auction_type,
            "Тип контракта"                                   AS contract_type,
            "Форма контракта"                                 AS contract_form,
            CAST("Срок оплаты" AS INTEGER)                    AS pay_term_days,
            CAST("Срок поставки" AS INTEGER)                  AS delivery_term_days,
            "Упаковка"                                        AS packaging,
            "ИНН продавца"                                    AS seller_inn,
            {_date('Дата регистрации')}                       AS reg_date,
            {_date('Действует до')}                           AS valid_until
        FROM {_csv(os.path.join(DATA_DIR, 'contracts.csv'))}
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW participants AS
        SELECT "ИНН" AS inn, "Наименование" AS name, "Регион" AS region,
               "Вид деятельности" AS activity, "Роль" AS role,
               "Плательщик НДС" AS vat_payer
        FROM {_csv(os.path.join(DATA_DIR, 'participants.csv'))}
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW brokers AS
        SELECT "Код брокера" AS code, "Наименование" AS name,
               "ИНН" AS inn, "Регион" AS region
        FROM {_csv(os.path.join(DATA_DIR, 'brokers.csv'))}
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW rates AS
        SELECT {_date('Дата')} AS rate_date, {_num('Курс')} AS rate
        FROM {_csv(os.path.join(DATA_DIR, 'usd_rates.csv'))}
        """
    )
    con.execute(
        """
        CREATE OR REPLACE VIEW deals_enriched AS
        SELECT
            d.*,
            c.currency,
            c.product_group,
            c.product_category,
            c.tnved_code,
            c.base_price,
            r.rate                                              AS usd_rate,
            d.qty * d.lot                                       AS volume,
            d.amount / NULLIF(d.qty * d.lot, 0)                 AS unit_price,
            CASE WHEN c.currency = 'Доллар США'
                 THEN d.amount * r.rate
                 ELSE d.amount END                              AS amount_uzs
        FROM deals d
        LEFT JOIN contracts c USING (contract_no)
        LEFT JOIN rates r ON r.rate_date = d.deal_date
        """
    )
    return con


def query(sql: str, params: list | None = None) -> list[tuple]:
    return connection().execute(sql, params or []).fetchall()


def one(sql: str, params: list | None = None):
    row = query(sql, params)
    return row[0] if row else None


def scalar(sql: str, params: list | None = None):
    row = one(sql, params)
    return row[0] if row else None


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _r(value, digits: int = 2):
    return None if value is None else round(float(value), digits)


def compute_metrics() -> dict:
    """Recompute the reference metrics straight from the CSV registry."""
    statuses = {
        status: int(count)
        for status, count in query(
            "SELECT status, count(*) FROM deals GROUP BY 1 ORDER BY 1"
        )
    }
    contract_types = {
        name: int(count)
        for name, count in query(
            "SELECT contract_type, count(*) FROM deals GROUP BY 1 ORDER BY 1"
        )
    }
    contract_currencies = {
        name: int(count)
        for name, count in query(
            "SELECT currency, count(*) FROM contracts GROUP BY 1 ORDER BY 1"
        )
    }
    deals_by_year = {
        str(year): int(count)
        for year, count in query(
            "SELECT year(deal_date), count(*) FROM deals GROUP BY 1 ORDER BY 1"
        )
    }

    def deals_with(product: str, year: int) -> int:
        return int(
            scalar(
                "SELECT count(*) FROM deals "
                "WHERE year(deal_date) = ? AND lower(product_name) LIKE ?",
                [year, f"%{product}%"],
            )
        )

    wheat = "пшениц"
    cotton = "хлопок"

    top_region = one(
        "SELECT region, count(*) AS deals FROM deals "
        "GROUP BY 1 ORDER BY deals DESC, region LIMIT 1"
    )
    top_seller = one(
        "SELECT seller_inn, sum(amount_uzs) AS amount_uzs, count(*) AS deals "
        "FROM deals_enriched WHERE year(deal_date) = 2024 "
        "GROUP BY 1 ORDER BY amount_uzs DESC, seller_inn LIMIT 1"
    )

    return {
        "dataset": {
            "deals_total": int(scalar("SELECT count(*) FROM deals")),
            "deals_by_year": deals_by_year,
            "deal_date_min": _iso(scalar("SELECT min(deal_date) FROM deals")),
            "deal_date_max": _iso(scalar("SELECT max(deal_date) FROM deals")),
            "contracts_total": int(scalar("SELECT count(*) FROM contracts")),
            "participants_total": int(scalar("SELECT count(*) FROM participants")),
            "brokers_total": int(scalar("SELECT count(*) FROM brokers")),
            "usd_rate_days": int(scalar("SELECT count(*) FROM rates")),
            "usd_rate_min_date": _iso(scalar("SELECT min(rate_date) FROM rates")),
            "usd_rate_max_date": _iso(scalar("SELECT max(rate_date) FROM rates")),
        },
        "statuses": statuses,
        "contract_types": contract_types,
        "contract_currencies": contract_currencies,
        "aggregates": {
            "amount_uzs_total": _r(scalar("SELECT sum(amount_uzs) FROM deals_enriched")),
            "volume_total": _r(scalar("SELECT sum(volume) FROM deals_enriched"), 3),
            "amount_uzs_2024": _r(
                scalar(
                    "SELECT sum(amount_uzs) FROM deals_enriched "
                    "WHERE year(deal_date) = 2024"
                )
            ),
            "deals_2024": int(deals_by_year.get("2024", 0)),
            "deals_2025": int(deals_by_year.get("2025", 0)),
            "deals_on_2026_10_30": int(
                scalar(
                    "SELECT count(*) FROM deals WHERE deal_date = DATE '2026-10-30'"
                )
            ),
            "annulled_deals": int(statuses.get("Аннулирован(Не оплачен)", 0)),
            "export_deals": int(contract_types.get("Экспортный", 0)),
            "new_deals": int(statuses.get("Новый", 0)),
            "fully_delivered_deals": int(statuses.get("Исполнен", 0)),
            "partially_delivered_deals": int(statuses.get("Исполнен(Частично)", 0)),
            "wheat_2025_deals": deals_with(wheat, 2025),
            "wheat_2025_volume": _r(
                scalar(
                    "SELECT sum(volume) FROM deals_enriched "
                    "WHERE year(deal_date) = 2025 AND lower(product_name) LIKE '%пшениц%'"
                ),
                3,
            ),
            "wheat_2025_amount_uzs": _r(
                scalar(
                    "SELECT sum(amount_uzs) FROM deals_enriched "
                    "WHERE year(deal_date) = 2025 AND lower(product_name) LIKE '%пшениц%'"
                )
            ),
            "wheat_2025_avg_unit_price": _r(
                scalar(
                    "SELECT sum(amount_uzs) / sum(volume) FROM deals_enriched "
                    "WHERE year(deal_date) = 2025 AND lower(product_name) LIKE '%пшениц%'"
                ),
                6,
            ),
            "cotton_2025_volume": _r(
                scalar(
                    "SELECT sum(volume) FROM deals_enriched "
                    "WHERE year(deal_date) = 2025 AND lower(product_name) LIKE '%хлопок%'"
                ),
                3,
            ),
            "cotton_2025_deals": deals_with(cotton, 2025),
            "seller_921912462_deals": int(
                scalar(
                    "SELECT count(*) FROM deals WHERE seller_inn = '921912462'"
                )
            ),
            "contract_100034_deals": int(
                scalar("SELECT count(*) FROM deals WHERE contract_no = 100034")
            ),
            "broker_BK001_seller_deals": int(
                scalar(
                    "SELECT count(*) FROM deals WHERE seller_broker = 'BK001'"
                )
            ),
            "usd_rate_2025_03_15": _r(rate_on("2025-03-15"), 6),
            "top_region_by_deals": {
                "region": top_region[0],
                "deals": int(top_region[1]),
            },
            "top_seller_2024_by_amount_uzs": {
                "inn": top_seller[0],
                "amount_uzs": _r(top_seller[1]),
                "deals": int(top_seller[2]),
            },
            "export_amount_usd_2024": _r(
                scalar(
                    "SELECT sum(amount) FROM deals_enriched "
                    "WHERE year(deal_date) = 2024 AND contract_type = 'Экспортный'"
                )
            ),
            "export_amount_uzs_2024": _r(
                scalar(
                    "SELECT sum(amount_uzs) FROM deals_enriched "
                    "WHERE year(deal_date) = 2024 AND contract_type = 'Экспортный'"
                )
            ),
        },
    }


def deal(deal_no: int) -> dict | None:
    row = query(
        "SELECT deal_no, deal_date, product_name, qty, lot, unit, amount, "
        "currency, status, contract_no, seller_inn, buyer_inn FROM deals_enriched "
        "WHERE deal_no = ?",
        [int(deal_no)],
    )
    if not row:
        return None
    keys = (
        "deal_no",
        "deal_date",
        "product_name",
        "qty",
        "lot",
        "unit",
        "amount",
        "currency",
        "status",
        "contract_no",
        "seller_inn",
        "buyer_inn",
    )
    result = dict(zip(keys, row[0]))
    result["deal_date"] = _iso(result["deal_date"])
    return result


def company(inn: str) -> dict | None:
    row = query("SELECT inn, name, region, activity, role, vat_payer FROM participants WHERE inn = ?", [str(inn)])
    if not row:
        return None
    return dict(zip(("inn", "name", "region", "activity", "role", "vat_payer"), row[0]))


def broker(code: str) -> dict | None:
    row = query("SELECT code, name, inn, region FROM brokers WHERE code = ?", [str(code)])
    if not row:
        return None
    return dict(zip(("code", "name", "inn", "region"), row[0]))


def rate_on(value: str | date) -> float | None:
    if isinstance(value, date):
        d = value
    else:
        text = str(value).strip()
        for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                d = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"unsupported date: {value!r}")
    result = scalar("SELECT rate FROM rates WHERE rate_date = ?", [d])
    return None if result is None else float(result)


def first_deal_of_year(year: int) -> dict | None:
    row = one(
        "SELECT deal_no FROM deals WHERE year(deal_date) = ? ORDER BY deal_no LIMIT 1",
        [int(year)],
    )
    return deal(row[0]) if row else None


def first_export_deal_of_year(year: int) -> dict | None:
    row = one(
        "SELECT deal_no FROM deals WHERE year(deal_date) = ? "
        "AND contract_type = 'Экспортный' ORDER BY deal_no LIMIT 1",
        [int(year)],
    )
    return deal(row[0]) if row else None


def first_internal_deal_of_year(year: int) -> dict | None:
    row = one(
        "SELECT deal_no FROM deals WHERE year(deal_date) = ? "
        "AND contract_type = 'Внутренний' ORDER BY deal_no LIMIT 1",
        [int(year)],
    )
    return deal(row[0]) if row else None


if __name__ == "__main__":
    import json

    print(json.dumps(compute_metrics(), ensure_ascii=False, indent=2))
