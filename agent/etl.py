"""Загрузка CSV реестра UZEX в DuckDB.

Запуск:
    python -m agent.etl                 # из папки проекта
    python -m agent.etl --data DIR --db FILE
"""

from __future__ import annotations

import argparse
import pathlib
import time

import duckdb

from .textutil import translit

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DATA = PROJECT_ROOT / "uzex_spot_data"
DEFAULT_DB = PROJECT_ROOT / "data" / "uzex.duckdb"

# Пробелы, которыми разделяют тысячи: обычный, неразрывный, узкий неразрывный.
_SPACE_STRIP = "replace(replace(replace({col}, ' ', ''), chr(160), ''), chr(8239), '')"


def _num(col: str) -> str:
    return f"TRY_CAST({_SPACE_STRIP.format(col=col)} AS DOUBLE)"


def _int(col: str) -> str:
    return f"TRY_CAST({_SPACE_STRIP.format(col=col)} AS BIGINT)"


def _date(col: str) -> str:
    return (
        f"CASE WHEN trim({col}) = '' THEN NULL "
        f"ELSE TRY_STRPTIME(trim({col}), '%d.%m.%Y')::DATE END"
    )


def _str(col: str) -> str:
    return f"nullif(trim({col}), '')"


def _norm(col: str) -> str:
    """Нормализация текста для склейки вариантов написания товара."""
    return (
        f"trim(regexp_replace(regexp_replace(lower(replace({col}, 'ё', 'е')), "
        f"'[^a-zа-я0-9]+', ' ', 'g'), '\\s+', ' ', 'g'))"
    )


def _read(data: pathlib.Path, pattern: str) -> str:
    path = (data / pattern).as_posix().replace("'", "''")
    return (
        f"read_csv('{path}', header = true, delim = ',', quote = '\"', "
        f"escape = '\"', all_varchar = true, ignore_errors = true)"
    )


DEALS_SQL = f"""
CREATE OR REPLACE TABLE deals AS
SELECT
    {_int('"Номер сделки"')}                AS deal_no,
    {_date('"Дата сделки"')}                AS deal_date,
    TRY_CAST(trim("Время сделки") AS TIME)  AS deal_time,
    {_str('"Тип торгов"')}                  AS auction_type,
    {_num('"Количество"')}                  AS qty,
    {_num('"Сумма сделки"')}                AS amount,
    {_num('"Стартовая цена"')}              AS start_price,
    {_int('"Номер контракта"')}             AS contract_no,
    {_str('"Наименование товара"')}         AS product_name,
    {_num('"Лот"')}                         AS lot,
    {_str('"Единица измерения"')}           AS unit,
    {_str('"Регион"')}                      AS region,
    {_str('"Тип контракта"')}               AS contract_type,
    {_str('"Форма контракта"')}             AS contract_form,
    {_str('"Состояние сделки"')}            AS status,
    {_str('"ИНН продавца"')}                AS seller_inn,
    {_str('"ИНН покупателя"')}              AS buyer_inn,
    {_str('"Брокер продавца"')}             AS seller_broker,
    {_str('"Брокер покупателя"')}           AS buyer_broker,
    {_date('"Дата оплаты"')}                AS pay_date,
    {_date('"Дата поставки"')}              AS delivery_date,
    {_num('"Поставлено"')}                  AS delivered
FROM {_read(DEFAULT_DATA, 'deals_*.csv')}
"""

CONTRACTS_SQL = f"""
CREATE OR REPLACE TABLE contracts AS
SELECT
    {_int('"Номер контракта"')}             AS contract_no,
    {_str('"Наименование продукта"')}       AS product_name,
    {_str('"Марка товара"')}                AS brand,
    {_str('"Размер товара"')}               AS size,
    {_str('"Сорт товара"')}                 AS grade,
    {_str('"Тип товара"')}                  AS kind,
    {_str('"Класс товара"')}                AS class_name,
    {_str('"Код ТН ВЭД"')}                  AS tnved_code,
    {_str('"Группа товара"')}               AS product_group,
    {_str('"Категория товара"')}            AS product_category,
    {_str('"Отрасль промышленности"')}      AS industry,
    {_str('"Единица измерения"')}           AS unit,
    {_num('"Лот"')}                         AS lot,
    {_str('"Валюта"')}                      AS currency,
    {_num('"Базисная цена"')}               AS base_price,
    {_str('"Сумма залога"')}                AS deposit,
    {_str('"Базис поставки"')}              AS delivery_basis,
    {_str('"Регион"')}                      AS region,
    {_str('"Адрес склада"')}                AS warehouse_address,
    {_str('"Тип торгов"')}                  AS auction_type,
    {_str('"Тип контракта"')}               AS contract_type,
    {_str('"Форма контракта"')}             AS contract_form,
    TRY_CAST(trim("Срок оплаты") AS INTEGER)   AS pay_term_days,
    TRY_CAST(trim("Срок поставки") AS INTEGER) AS delivery_term_days,
    {_str('"Упаковка"')}                    AS packaging,
    {_str('"ИНН продавца"')}                AS seller_inn,
    {_date('"Дата регистрации"')}           AS reg_date,
    {_date('"Действует до"')}               AS valid_until,
    {_norm('"Наименование продукта"')}      AS product_key
FROM {_read(DEFAULT_DATA, 'contracts.csv')}
"""

# Каноническое (самое частое) написание товара для каждого ключа.
PRODUCT_NAMES_SQL = """
CREATE OR REPLACE TABLE product_names AS
SELECT product_key, arg_max(product_name, cnt) AS canonical_name
FROM (
    SELECT product_key, product_name, count(*) AS cnt
    FROM contracts WHERE product_key IS NOT NULL
    GROUP BY 1, 2
)
GROUP BY 1
"""

PARTICIPANTS_SQL = f"""
CREATE OR REPLACE TABLE participants AS
SELECT
    {_str('"ИНН"')}             AS inn,
    {_str('"Наименование"')}    AS name,
    {_str('"Регион"')}          AS region,
    {_str('"Вид деятельности"')} AS activity,
    {_str('"Роль"')}            AS role,
    {_str('"Плательщик НДС"')}  AS vat_payer
FROM {_read(DEFAULT_DATA, 'participants.csv')}
"""

BROKERS_SQL = f"""
CREATE OR REPLACE TABLE brokers AS
SELECT
    {_str('"Код брокера"')}  AS code,
    {_str('"Наименование"')} AS name,
    {_str('"ИНН"')}          AS inn,
    {_str('"Регион"')}       AS region
FROM {_read(DEFAULT_DATA, 'brokers.csv')}
"""

RATES_SQL = f"""
CREATE OR REPLACE TABLE usd_rates AS
SELECT
    {_date('"Дата"')} AS rate_date,
    {_num('"Курс"')}  AS rate
FROM {_read(DEFAULT_DATA, 'usd_rates.csv')}
"""

# Обогащённое представление: валюта/классификация из контракта,
# объём, цена за единицу и сумма в сумах (экспорт пересчитывается по курсу дня).
VIEW_SQL = """
CREATE OR REPLACE VIEW deals_enriched AS
SELECT
    d.*,
    c.product_name                             AS contract_product_name,
    c.product_key                              AS product_key,
    pn.canonical_name                          AS product_canon,
    c.currency,
    c.tnved_code,
    c.product_group,
    c.product_category,
    c.industry,
    c.base_price,
    c.pay_term_days,
    c.delivery_term_days,
    d.qty * d.lot                              AS volume,
    CASE WHEN d.qty * d.lot > 0
         THEN d.amount / (d.qty * d.lot) END   AS unit_price,
    CASE WHEN c.currency = 'Доллар США'
         THEN d.amount * r.rate ELSE d.amount END AS amount_uzs,
    r.rate                                     AS usd_rate,
    s.name                                     AS seller_name,
    b.name                                     AS buyer_name,
    s.name_latin                               AS seller_name_latin,
    b.name_latin                               AS buyer_name_latin,
    sb.name                                    AS seller_broker_name,
    bb.name                                    AS buyer_broker_name
FROM deals d
LEFT JOIN contracts c USING (contract_no)
LEFT JOIN product_names pn USING (product_key)
LEFT JOIN usd_rates r ON r.rate_date = d.deal_date
LEFT JOIN participants s ON s.inn = d.seller_inn
LEFT JOIN participants b ON b.inn = d.buyer_inn
LEFT JOIN brokers sb ON sb.code = d.seller_broker
LEFT JOIN brokers bb ON bb.code = d.buyer_broker
"""

# Помесячные агрегаты для быстрых «динамика/тренд» вопросов.
MONTHLY_SQL = """
CREATE OR REPLACE TABLE deals_monthly AS
SELECT
    date_trunc('month', deal_date) AS month,
    product_group,
    product_category,
    contract_type,
    count(*)              AS deals,
    sum(volume)           AS volume,
    sum(amount_uzs)       AS amount_uzs,
    avg(unit_price)       AS avg_unit_price,
    avg(usd_rate)         AS avg_usd_rate
FROM deals_enriched
GROUP BY ALL
"""


def _add_latin_names(con: duckdb.DuckDBPyConnection) -> None:
    """Добавляет name_latin участникам и брокерам для поиска в любом алфавите."""
    for table in ("participants", "brokers"):
        rows = con.execute(f"SELECT inn, name FROM {table}").fetchall()
        con.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS name_latin VARCHAR")
        con.execute("CREATE OR REPLACE TEMP TABLE _latin(inn VARCHAR, name_latin VARCHAR)")
        con.executemany(
            "INSERT INTO _latin VALUES (?, ?)",
            [(inn, translit(name or "")) for inn, name in rows],
        )
        con.execute(
            f"UPDATE {table} SET name_latin = l.name_latin "
            f"FROM _latin l WHERE {table}.inn = l.inn"
        )
        con.execute("DROP TABLE _latin")


def build(data_dir: pathlib.Path = DEFAULT_DATA, db_path: pathlib.Path = DEFAULT_DB) -> None:
    global DEALS_SQL, CONTRACTS_SQL, PARTICIPANTS_SQL, BROKERS_SQL, RATES_SQL
    if data_dir != DEFAULT_DATA:
        DEALS_SQL = DEALS_SQL.replace(_read(DEFAULT_DATA, "deals_*.csv"), _read(data_dir, "deals_*.csv"))
        CONTRACTS_SQL = CONTRACTS_SQL.replace(_read(DEFAULT_DATA, "contracts.csv"), _read(data_dir, "contracts.csv"))
        PARTICIPANTS_SQL = PARTICIPANTS_SQL.replace(_read(DEFAULT_DATA, "participants.csv"), _read(data_dir, "participants.csv"))
        BROKERS_SQL = BROKERS_SQL.replace(_read(DEFAULT_DATA, "brokers.csv"), _read(data_dir, "brokers.csv"))
        RATES_SQL = RATES_SQL.replace(_read(DEFAULT_DATA, "usd_rates.csv"), _read(data_dir, "usd_rates.csv"))

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    started = time.time()
    con = duckdb.connect(str(db_path))
    try:
        con.execute("SET threads TO 4")
        for name, sql in [
            ("deals", DEALS_SQL),
            ("contracts", CONTRACTS_SQL),
            ("participants", PARTICIPANTS_SQL),
            ("brokers", BROKERS_SQL),
            ("usd_rates", RATES_SQL),
            ("product_names", PRODUCT_NAMES_SQL),
        ]:
            t0 = time.time()
            con.execute(sql)
            rows = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            print(f"  {name:<13} {rows:>8} rows  ({time.time() - t0:.1f}s)")
        _add_latin_names(con)
        con.execute(VIEW_SQL)
        con.execute(MONTHLY_SQL)
        rows = con.execute("SELECT count(*) FROM deals_monthly").fetchone()[0]
        print(f"  deals_monthly {rows:>8} rows")
    finally:
        con.close()
    print(f"Готово за {time.time() - started:.1f}s -> {db_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="CSV -> DuckDB ETL для UZEX")
    parser.add_argument("--data", type=pathlib.Path, default=DEFAULT_DATA)
    parser.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    args = parser.parse_args()
    build(args.data, args.db)


if __name__ == "__main__":
    main()
