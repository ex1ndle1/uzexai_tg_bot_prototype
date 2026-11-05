"""Инструменты (tools) для LLM-оркестратора.

Каждый инструмент — детерминированный SQL-запрос к DuckDB. LLM не видит
данные напрямую: только JSON-результаты этих функций. Это главный барьер
против выдумывания цифр.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Callable

from . import db
from .textutil import normalize_text, translit

MAX_ROWS = 100
CURRENCY_USD = "Доллар США"

# ---------------------------------------------------------------------------
# фильтры и парсинг
# ---------------------------------------------------------------------------

DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d", "%d-%m-%Y")


# Узбекские названия товаров -> русские, как в реестре.
UZ_PRODUCT_PHRASES = [
    ("paxta yog", "масло хлопковое"),
    ("paxta", "хлопок"),
    ("bug doy", "пшеница"),
    ("bugdoy", "пшеница"),
    ("sholi", "рис"),
    ("shakar", "сахар"),
    ("sement", "цемент"),
    ("cement", "цемент"),
    ("polietilen", "полиэтилен"),
    ("benzin", "бензин"),
]

UZ_PRODUCT_TOKENS = {
    "un": "мука", "uni": "мука", "unlar": "мука",
    "paxta": "хлопок", "sholi": "рис", "shakar": "сахар",
    "tuz": "соль", "yog": "масло", "mis": "медь",
    "bugdoy": "пшеница", "bug": "пшеница", "doy": "",
}


def product_query(value: str) -> str:
    """Нормализует запрос товара: узбекские слова -> русские термины реестра."""
    key = normalize_text(value)
    for src, dst in UZ_PRODUCT_PHRASES:
        key = key.replace(src, normalize_text(dst))
    tokens = [UZ_PRODUCT_TOKENS.get(t, t) for t in key.split()]
    # Если после перевода остались латинские модификаторы («pishiriq un» ->
    # «pishiriq мука»), отбрасываем их: в реестре товар называется «Мука».
    has_ru = any(re.fullmatch(r"[а-я0-9-]+", t) for t in tokens if t)
    tokens = [t for t in tokens if t and (not has_ru or re.fullmatch(r"[а-я0-9-]+", t))]
    return " ".join(tokens)


def _parse_date(value: str, end: bool = False) -> dt.date:
    value = value.strip()
    m = re.fullmatch(r"(\d{4})", value)
    if m:
        year = int(m.group(1))
        return dt.date(year, 12, 31) if end else dt.date(year, 1, 1)
    m = re.fullmatch(r"(\d{4})[-./](\d{1,2})", value)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if end:
            nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
            return nxt - dt.timedelta(days=1)
        return dt.date(year, month, 1)
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Не удалось разобрать дату: {value!r}")


def _company_clause(
    inn_col: str, name_col: str, value: str, latin_col: str | None = None
) -> tuple[str, list[Any]]:
    value = value.strip()
    if re.fullmatch(r"\d{9}", value):
        return f"{inn_col} = ?", [value]
    if latin_col:
        return (
            f"(coalesce({name_col}, '') ILIKE ? OR coalesce({latin_col}, '') ILIKE ?)",
            [f"%{value}%", f"%{translit(value)}%"],
        )
    return f"coalesce({name_col}, '') ILIKE ?", [f"%{value}%"]


def _build_filters(
    product: str | None = None,
    seller: str | None = None,
    buyer: str | None = None,
    region: str | None = None,
    status: str | None = None,
    contract_type: str | None = None,
    contract_form: str | None = None,
    auction_type: str | None = None,
    tnved: str | None = None,
    product_category: str | None = None,
    product_group: str | None = None,
    currency: str | None = None,
    unit: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if product:
        raw = product.strip()
        key = product_query(raw)
        clauses.append(
            "(product_key ILIKE ? OR product_name ILIKE ? OR contract_product_name ILIKE ?)"
        )
        params += [f"%{key}%", f"%{raw}%", f"%{raw}%"]
    if seller:
        clause, p = _company_clause("seller_inn", "seller_name", seller, "seller_name_latin")
        clauses.append(clause)
        params += p
    if buyer:
        clause, p = _company_clause("buyer_inn", "buyer_name", buyer, "buyer_name_latin")
        clauses.append(clause)
        params += p
    if region:
        clauses.append("region ILIKE ?")
        params.append(f"%{region.strip()}%")
    if status:
        clauses.append("status ILIKE ?")
        params.append(f"%{status.strip()}%")
    if contract_type:
        clauses.append("contract_type ILIKE ?")
        params.append(f"%{contract_type.strip()}%")
    if contract_form:
        clauses.append("contract_form ILIKE ?")
        params.append(f"%{contract_form.strip()}%")
    if auction_type:
        clauses.append("auction_type ILIKE ?")
        params.append(f"%{auction_type.strip()}%")
    if tnved:
        clauses.append("tnved_code = ?")
        params.append(tnved.strip())
    if product_category:
        clauses.append("product_category ILIKE ?")
        params.append(f"%{product_category.strip()}%")
    if product_group:
        clauses.append("product_group ILIKE ?")
        params.append(f"%{product_group.strip()}%")
    if currency:
        clauses.append("currency ILIKE ?")
        params.append(f"%{currency.strip()}%")
    if unit:
        clauses.append("unit ILIKE ?")
        params.append(f"%{unit.strip()}%")
    if date_from:
        clauses.append("deal_date >= ?")
        params.append(_parse_date(date_from))
    if date_to:
        clauses.append("deal_date <= ?")
        params.append(_parse_date(date_to, end=True))

    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


# ---------------------------------------------------------------------------
# группировки и метрики
# ---------------------------------------------------------------------------

GROUP_EXPR: dict[str, str] = {
    "product": "coalesce(product_canon, contract_product_name, product_name)",
    "product_category": "product_category",
    "product_group": "product_group",
    "tnved": "tnved_code",
    "industry": "industry",
    "region": "region",
    "unit": "unit",
    "month": "strftime(deal_date, '%Y-%m')",
    "year": "strftime(deal_date, '%Y')",
    "status": "status",
    "contract_type": "contract_type",
    "contract_form": "contract_form",
    "auction_type": "auction_type",
    "currency": "currency",
    "seller": "seller_name",
    "seller_inn": "seller_inn",
    "buyer": "buyer_name",
    "buyer_inn": "buyer_inn",
    "seller_broker": "seller_broker_name",
    "buyer_broker": "buyer_broker_name",
}

METRIC_EXPR: dict[str, str] = {
    "deals": "count(*)",
    "volume": "sum(volume)",
    "amount": "sum(amount)",          # в валюте контракта (смешивать нельзя)
    "amount_uzs": "sum(amount_uzs)",  # всегда в сумах
    "delivered": "sum(delivered)",
    "avg_price": "sum(amount_uzs) / nullif(sum(volume), 0)",
    "avg_deal_amount": "avg(amount_uzs)",
}

METRIC_LABEL = {
    "deals": "Количество сделок",
    "volume": "Объём (в единицах измерения)",
    "amount": "Сумма в валюте контракта",
    "amount_uzs": "Сумма, сум",
    "delivered": "Поставлено (лотов)",
    "avg_price": "Средневзвешенная цена за единицу, сум",
    "avg_deal_amount": "Средняя сумма сделки, сум",
}


class ToolError(ValueError):
    """Ошибка входных параметров инструмента (показывается LLM)."""


# ---------------------------------------------------------------------------
# инструменты
# ---------------------------------------------------------------------------

def dataset_overview() -> dict[str, Any]:
    """Общая информация о реестре: объёмы, период, справочники."""
    totals = db.fetchone(
        """
        SELECT count(*) AS deals, min(deal_date) AS date_from, max(deal_date) AS date_to,
               count(DISTINCT contract_no) AS contracts_used,
               count(DISTINCT seller_inn) AS sellers,
               count(DISTINCT buyer_inn) AS buyers
        FROM deals_enriched
        """
    )
    counts = db.fetchone(
        """
        SELECT (SELECT count(*) FROM contracts) AS contracts,
               (SELECT count(*) FROM participants) AS participants,
               (SELECT count(*) FROM brokers) AS brokers,
               (SELECT count(*) FROM usd_rates) AS usd_rate_days,
               (SELECT count(*) FROM product_names) AS distinct_products
        """
    )
    categories = db.fetch(
        "SELECT product_category AS category, count(*) AS contracts FROM contracts "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    statuses = db.fetch(
        "SELECT status, count(*) AS deals FROM deals GROUP BY 1 ORDER BY 2 DESC"
    )
    units = db.fetch("SELECT unit, count(*) AS deals FROM deals GROUP BY 1 ORDER BY 2 DESC")
    top_products = db.fetch(
        "SELECT coalesce(product_canon, contract_product_name, product_name) AS product, "
        "count(*) AS deals FROM deals_enriched GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
    )
    return {"totals": totals, "reference": counts, "categories": categories,
            "statuses": statuses, "units": units, "top_products": top_products}


def get_deal(deal_no: int) -> dict[str, Any]:
    """Полная карточка сделки по номеру."""
    rows = db.fetch("SELECT * FROM deals_enriched WHERE deal_no = ?", [int(deal_no)])
    if not rows:
        return {"found": False, "deal_no": deal_no,
                "note": "Сделки с таким номером в реестре нет"}
    return {"found": True, "deal": rows[0]}


def search_deals(
    product: str | None = None,
    seller: str | None = None,
    buyer: str | None = None,
    region: str | None = None,
    status: str | None = None,
    contract_type: str | None = None,
    contract_form: str | None = None,
    auction_type: str | None = None,
    tnved: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Поиск сделок по фильтрам, свежие сверху."""
    where, params = _build_filters(
        product, seller, buyer, region, status, contract_type, contract_form,
        auction_type, tnved, date_from=date_from, date_to=date_to,
    )
    limit = max(1, min(int(limit), MAX_ROWS))
    sql = f"""
        SELECT deal_no, deal_date, deal_time, product_name, contract_product_name,
               qty, lot, unit, amount, currency, amount_uzs, unit_price,
               status, region, contract_type, contract_form, auction_type,
               seller_inn, seller_name, buyer_inn, buyer_name
        FROM deals_enriched {where}
        ORDER BY deal_date DESC, deal_no DESC
        LIMIT {limit}
    """
    rows = db.fetch(sql, params)
    total = db.fetchone(f"SELECT count(*) AS n FROM deals_enriched {where}", params)
    return {"returned": len(rows), "matched": total["n"] if total else 0, "deals": rows}


def aggregate_deals(
    group_by: str,
    metric: str = "amount_uzs",
    product: str | None = None,
    seller: str | None = None,
    buyer: str | None = None,
    region: str | None = None,
    status: str | None = None,
    contract_type: str | None = None,
    contract_form: str | None = None,
    auction_type: str | None = None,
    tnved: str | None = None,
    product_category: str | None = None,
    currency: str | None = None,
    unit: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 15,
) -> dict[str, Any]:
    """Агрегация сделок: group_by x metric с фильтрами, топ-N."""
    if group_by not in GROUP_EXPR:
        raise ToolError(
            f"group_by={group_by!r} не поддерживается. Допустимо: {sorted(GROUP_EXPR)}"
        )
    if metric not in METRIC_EXPR:
        raise ToolError(
            f"metric={metric!r} не поддерживается. Допустимо: {sorted(METRIC_EXPR)}"
        )
    where, params = _build_filters(
        product, seller, buyer, region, status, contract_type, contract_form,
        auction_type, tnved, product_category, None, currency, unit,
        date_from, date_to,
    )
    expr = GROUP_EXPR[group_by]
    extra_expr = {
        "seller": "seller_inn",
        "buyer": "buyer_inn",
        "seller_broker": "seller_broker",
        "buyer_broker": "buyer_broker",
    }.get(group_by)
    metric_sql = METRIC_EXPR[metric]
    limit = max(1, min(int(limit), MAX_ROWS))
    if extra_expr:
        select_extra, group_extra = f", {extra_expr} AS code", ", 2"
    else:
        select_extra, group_extra = "", ""
    sql = f"""
        SELECT {expr} AS label{select_extra}, {metric_sql} AS value, count(*) AS deals
        FROM deals_enriched {where}
        GROUP BY 1{group_extra}
        HAVING {expr} IS NOT NULL
        ORDER BY value DESC NULLS LAST
        LIMIT {limit}
    """
    rows = db.fetch(sql, params)
    summary = db.fetchone(
        f"SELECT count(*) AS deals, sum(volume) AS volume, sum(amount_uzs) AS amount_uzs "
        f"FROM deals_enriched {where}",
        params,
    )
    return {
        "group_by": group_by,
        "metric": metric,
        "metric_label": METRIC_LABEL[metric],
        "summary": summary,
        "rows": rows,
    }


def product_stats(
    product: str,
    date_from: str | None = None,
    date_to: str | None = None,
    region: str | None = None,
    contract_type: str | None = None,
) -> dict[str, Any]:
    """Сводка по товару: объёмы, суммы, цены, топ продавцов/покупателей, статусы."""
    where, params = _build_filters(
        product, region=region, contract_type=contract_type,
        date_from=date_from, date_to=date_to,
    )
    totals = db.fetchone(
        f"""
        SELECT count(*) AS deals, sum(volume) AS volume, sum(amount_uzs) AS amount_uzs,
               sum(amount_uzs) / nullif(sum(volume), 0) AS weighted_avg_price_uzs,
               min(amount_uzs / nullif(volume, 0)) AS min_price_uzs,
               max(amount_uzs / nullif(volume, 0)) AS max_price_uzs,
               min(deal_date) AS first_deal, max(deal_date) AS last_deal,
               count(DISTINCT seller_inn) AS sellers, count(DISTINCT buyer_inn) AS buyers
        FROM deals_enriched {where}
        """,
        params,
    )
    if not totals or not totals["deals"]:
        return {"found": False, "product": product,
                "note": "В реестре нет сделок по такому товару за указанный период"}
    statuses = db.fetch(
        f"SELECT status, count(*) AS deals FROM deals_enriched {where} GROUP BY 1 ORDER BY 2 DESC",
        params,
    )
    sellers = db.fetch(
        f"""SELECT seller_name AS name, seller_inn AS inn, count(*) AS deals,
                   sum(volume) AS volume, sum(amount_uzs) AS amount_uzs
            FROM deals_enriched {where}
            GROUP BY 1, 2 ORDER BY amount_uzs DESC LIMIT 5""",
        params,
    )
    buyers = db.fetch(
        f"""SELECT buyer_name AS name, buyer_inn AS inn, count(*) AS deals,
                   sum(volume) AS volume, sum(amount_uzs) AS amount_uzs
            FROM deals_enriched {where}
            GROUP BY 1, 2 ORDER BY amount_uzs DESC LIMIT 5""",
        params,
    )
    return {"found": True, "product_query": product, "totals": totals,
            "statuses": statuses, "top_sellers": sellers, "top_buyers": buyers}


def price_dynamics(
    product: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    granularity: str = "month",
    tnved: str | None = None,
    region: str | None = None,
    contract_type: str | None = None,
) -> dict[str, Any]:
    """Динамика цены (сум/единица), объёма и курса USD по периодам."""
    bucket = {
        "day": "strftime(deal_date, '%Y-%m-%d')",
        "week": "strftime(date_trunc('week', deal_date), '%Y-%m-%d')",
        "month": "strftime(deal_date, '%Y-%m')",
        "year": "strftime(deal_date, '%Y')",
    }.get(granularity)
    if bucket is None:
        raise ToolError("granularity должен быть: day, week, month или year")
    where, params = _build_filters(
        product, tnved=tnved, region=region, contract_type=contract_type,
        date_from=date_from, date_to=date_to,
    )
    rows = db.fetch(
        f"""
        SELECT {bucket} AS period,
               count(*) AS deals,
               sum(volume) AS volume,
               sum(amount_uzs) AS amount_uzs,
               sum(amount_uzs) / nullif(sum(volume), 0) AS price_uzs_per_unit,
               avg(usd_rate) AS usd_rate
        FROM deals_enriched {where}
        GROUP BY 1 ORDER BY 1
        """,
        params,
    )
    return {"granularity": granularity, "rows": rows,
            "note": "price_uzs_per_unit — средневзвешенная цена за единицу измерения в сумах"}


def get_contract(contract_no: int) -> dict[str, Any]:
    """Карточка контракта по номеру."""
    rows = db.fetch("SELECT * FROM contracts WHERE contract_no = ?", [int(contract_no)])
    if not rows:
        return {"found": False, "contract_no": contract_no,
                "note": "Контракта с таким номером в реестре нет"}
    stats = db.fetchone(
        "SELECT count(*) AS deals, sum(volume) AS volume, sum(amount_uzs) AS amount_uzs, "
        "min(deal_date) AS first_deal, max(deal_date) AS last_deal "
        "FROM deals_enriched WHERE contract_no = ?",
        [int(contract_no)],
    )
    return {"found": True, "contract": rows[0], "deal_stats": stats}


def find_contracts(
    product: str | None = None,
    tnved: str | None = None,
    region: str | None = None,
    seller: str | None = None,
    currency: str | None = None,
    contract_form: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Поиск карточек контрактов."""
    clauses: list[str] = []
    params: list[Any] = []
    if product:
        raw = product.strip()
        key = product_query(raw)
        clauses.append("(product_key ILIKE ? OR product_name ILIKE ? "
                       "OR coalesce(brand,'') ILIKE ? "
                       "OR coalesce(grade,'') ILIKE ? OR coalesce(class_name,'') ILIKE ?)")
        params += [f"%{key}%", f"%{raw}%"] + [f"%{raw}%"] * 3
    if tnved:
        clauses.append("tnved_code = ?")
        params.append(tnved)
    if region:
        clauses.append("region ILIKE ?")
        params.append(f"%{region}%")
    if seller:
        clause, p = _company_clause("seller_inn", "''", seller)
        clauses.append(clause)
        params += p
    if currency:
        clauses.append("currency ILIKE ?")
        params.append(f"%{currency}%")
    if contract_form:
        clauses.append("contract_form ILIKE ?")
        params.append(f"%{contract_form}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    limit = max(1, min(int(limit), MAX_ROWS))
    rows = db.fetch(
        f"""
        SELECT contract_no, product_name, brand, size, grade, kind, class_name,
               tnved_code, product_category, unit, lot, currency, base_price,
               contract_type, contract_form, pay_term_days, delivery_term_days,
               region, seller_inn, reg_date, valid_until
        FROM contracts {where}
        ORDER BY reg_date DESC LIMIT {limit}
        """,
        params,
    )
    total = db.fetchone(f"SELECT count(*) AS n FROM contracts {where}", params)
    return {"returned": len(rows), "matched": total["n"] if total else 0, "contracts": rows}


def get_company(query: str, limit: int = 10) -> dict[str, Any]:
    """Найти компанию по ИНН (точно) или названию (подстрока)."""
    query = query.strip()
    limit = max(1, min(int(limit), MAX_ROWS))
    if re.fullmatch(r"\d{9}", query):
        rows = db.fetch("SELECT * FROM participants WHERE inn = ?", [query])
    else:
        rows = db.fetch(
            "SELECT * FROM participants WHERE name ILIKE ? OR coalesce(name_latin,'') ILIKE ? "
            "ORDER BY name LIMIT " + str(limit),
            [f"%{query}%", f"%{translit(query)}%"],
        )
    if not rows:
        return {"found": False, "query": query, "note": "Компания в реестре не найдена"}
    enriched = []
    for row in rows:
        stats = db.fetchone(
            """SELECT
                 (SELECT count(*) FROM deals WHERE seller_inn = ?) AS deals_as_seller,
                 (SELECT count(*) FROM deals WHERE buyer_inn = ?) AS deals_as_buyer,
                 (SELECT sum(amount_uzs) FROM deals_enriched WHERE seller_inn = ?) AS amount_as_seller,
                 (SELECT sum(amount_uzs) FROM deals_enriched WHERE buyer_inn = ?) AS amount_as_buyer
            """,
            [row["inn"], row["inn"], row["inn"], row["inn"]],
        )
        enriched.append({**row, "stats": stats})
    return {"found": True, "count": len(enriched), "companies": enriched}


def company_stats(
    inn: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Подробная статистика компании по ИНН: как продавец и как покупатель."""
    inn = inn.strip()
    info = db.fetchone("SELECT * FROM participants WHERE inn = ?", [inn])
    if not info:
        return {"found": False, "inn": inn,
                "note": "Компании с таким ИНН в реестре нет"}
    where, params = _build_filters(date_from=date_from, date_to=date_to)

    def side(role: str) -> dict[str, Any]:
        col_inn = "seller_inn" if role == "seller" else "buyer_inn"
        extra = f" AND {col_inn} = ?" if where else f" WHERE {col_inn} = ?"
        total = db.fetchone(
            f"SELECT count(*) AS deals, sum(volume) AS volume, sum(amount_uzs) AS amount_uzs, "
            f"min(deal_date) AS first_deal, max(deal_date) AS last_deal "
            f"FROM deals_enriched {where}{extra}",
            params + [inn],
        )
        if not total or not total["deals"]:
            return {"totals": total, "top_products": [], "top_counterparties": []}
        products = db.fetch(
            f"SELECT coalesce(product_canon, contract_product_name, product_name) AS product, count(*) AS deals, "
            f"sum(volume) AS volume, sum(amount_uzs) AS amount_uzs FROM deals_enriched {where}{extra} "
            f"GROUP BY 1 ORDER BY amount_uzs DESC LIMIT 10",
            params + [inn],
        )
        other_name = "buyer_name" if role == "seller" else "seller_name"
        other_inn = "buyer_inn" if role == "seller" else "seller_inn"
        parties = db.fetch(
            f"SELECT {other_name} AS name, {other_inn} AS inn, count(*) AS deals, "
            f"sum(amount_uzs) AS amount_uzs FROM deals_enriched {where}{extra} "
            f"GROUP BY 1, 2 ORDER BY amount_uzs DESC LIMIT 10",
            params + [inn],
        )
        return {"totals": total, "top_products": products, "top_counterparties": parties}

    return {
        "found": True,
        "company": info,
        "as_seller": side("seller"),
        "as_buyer": side("buyer"),
    }


def get_broker(query: str) -> dict[str, Any]:
    """Брокерская контора по коду (BK001), названию или ИНН + её оборот."""
    query = query.strip()
    if re.fullmatch(r"BK\d{3}", query, re.IGNORECASE):
        rows = db.fetch("SELECT * FROM brokers WHERE upper(code) = upper(?)", [query])
    elif re.fullmatch(r"\d{9}", query):
        rows = db.fetch("SELECT * FROM brokers WHERE inn = ?", [query])
    else:
        rows = db.fetch(
            "SELECT * FROM brokers WHERE name ILIKE ? OR coalesce(name_latin,'') ILIKE ? LIMIT 10",
            [f"%{query}%", f"%{translit(query)}%"],
        )
    if not rows:
        return {"found": False, "query": query, "note": "Брокер в реестре не найден"}

    result = []
    for row in rows:
        stats = db.fetchone(
            """SELECT
                 (SELECT count(*) FROM deals WHERE seller_broker = ?) AS deals_as_seller_broker,
                 (SELECT count(*) FROM deals WHERE buyer_broker = ?) AS deals_as_buyer_broker,
                 (SELECT sum(amount_uzs) FROM deals_enriched WHERE seller_broker = ?) AS amount_as_seller_broker,
                 (SELECT sum(amount_uzs) FROM deals_enriched WHERE buyer_broker = ?) AS amount_as_buyer_broker
            """,
            [row["code"], row["code"], row["code"], row["code"]],
        )
        result.append({**row, "stats": stats})
    return {"found": True, "count": len(result), "brokers": result}


def usd_rate(date_from: str | None = None, date_to: str | None = None) -> dict[str, Any]:
    """Курс доллара: на дату (точный или ближайший) либо диапазон."""
    if not date_from:
        rows = db.fetch(
            "SELECT rate_date, rate FROM usd_rates ORDER BY rate_date LIMIT 1"
        ) + db.fetch("SELECT rate_date, rate FROM usd_rates ORDER BY rate_date DESC LIMIT 1")
        return {"rows": rows, "note": "Диапазон курсов в реестре"}
    start = _parse_date(date_from)
    end = _parse_date(date_to, end=True) if date_to else start
    rows = db.fetch(
        "SELECT rate_date, rate FROM usd_rates WHERE rate_date BETWEEN ? AND ? "
        "ORDER BY rate_date",
        [start, end],
    )
    if not rows:
        nearest = db.fetch(
            "SELECT rate_date, rate FROM usd_rates ORDER BY abs(rate_date - ?) LIMIT 1",
            [start],
        )
        return {"found": False, "requested": date_from,
                "note": "Курса на эту дату нет", "nearest": nearest}
    if len(rows) == 1:
        return {"found": True, "date": rows[0]["rate_date"], "rate": rows[0]["rate"],
                "rows": rows}
    return {"found": True, "date_from": rows[0]["rate_date"],
            "date_to": rows[-1]["rate_date"], "min": min(r["rate"] for r in rows),
            "max": max(r["rate"] for r in rows),
            "avg": sum(r["rate"] for r in rows) / len(rows), "rows_count": len(rows)}


ALLOWED_TABLES = {
    "deals", "deals_enriched", "deals_monthly",
    "contracts", "participants", "brokers", "usd_rates",
}

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|pragma|"
    r"install|load|export|import|call|truncate|vacuum|checkpoint)\b",
    re.IGNORECASE,
)
FILE_FUNCS = re.compile(r"\b(read_csv|read_csv_auto|read_parquet|read_json|parquet_scan|glob)\b",
                        re.IGNORECASE)


def sql_query(sql: str) -> dict[str, Any]:
    """Fallback: произвольный read-only SELECT по разрешённым таблицам."""
    cleaned = sql.strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
        raise ToolError("Разрешены только SELECT/WITH запросы")
    if ";" in cleaned:
        raise ToolError("Разрешён только один SQL-запрос")
    if FORBIDDEN.search(cleaned):
        raise ToolError("Запрос содержит запрещённую операцию")
    if FILE_FUNCS.search(cleaned):
        raise ToolError("Чтение файлов из SQL запрещено")
    tables = set(re.findall(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", cleaned, re.IGNORECASE))
    unknown = tables - ALLOWED_TABLES
    if unknown:
        raise ToolError(f"Неизвестные таблицы: {sorted(unknown)}. Доступно: {sorted(ALLOWED_TABLES)}")
    # Внешний LIMIT гарантирует, что даже запрос без ограничения не вернёт всю базу.
    wrapped = f"SELECT * FROM ({cleaned}) AS sub LIMIT {MAX_ROWS}"
    try:
        rows = db.fetch(wrapped)
    except Exception as exc:  # ошибка SQL уходит LLM для исправления
        raise ToolError(f"Ошибка SQL: {exc}") from exc
    return {"rows": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# реестр инструментов + JSON-схемы для function calling
# ---------------------------------------------------------------------------

FilterProps = {
    "product": {"type": "string", "description": "Товар или его часть, регистронезависимо (например 'хлопок', 'пшеница'). Узбекские названия переводи на русский: paxta→хлопок, bug‘doy→пшеница, un→мука, sholi→рис, shakar→сахар, tuz→соль, yog‘→масло, mis→медь. Передавай базовое название товара."},
    "seller": {"type": "string", "description": "ИНН продавца (9 цифр) или часть названия"},
    "buyer": {"type": "string", "description": "ИНН покупателя (9 цифр) или часть названия"},
    "region": {"type": "string", "description": "Регион, например 'Toshkent shahri', 'Namangan viloyati'"},
    "status": {"type": "string", "description": "Состояние сделки: Новый, Не подписан, Заключен, Ждёт оплату, Оплачен, Исполнен, Исполнен(Частично), Аннулирован(Не оплачен)"},
    "contract_type": {"type": "string", "enum": ["Внутренний", "Экспортный"]},
    "contract_form": {"type": "string", "enum": ["Спот", "Форвард", "Фьючерс"]},
    "auction_type": {"type": "string", "enum": ["Английский аукцион", "Обратный аукцион", "Двусторонний аукцион"]},
    "tnved": {"type": "string", "description": "Код ТН ВЭД (до 6 цифр)"},
    "date_from": {"type": "string", "description": "Начало периода: YYYY-MM-DD, YYYY-MM или YYYY"},
    "date_to": {"type": "string", "description": "Конец периода включительно: YYYY-MM-DD, YYYY-MM или YYYY"},
    "currency": {"type": "string", "enum": ["Узбекский сум", "Доллар США"]},
    "unit": {"type": "string", "description": "Единица измерения: тонна, килограмм, литр, штука, куб.метр"},
}


def _props(*names: str, **extra: Any) -> dict[str, Any]:
    return {**{n: FilterProps[n] for n in names}, **extra}


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "dataset_overview",
            "description": "Общая справка о реестре: количество сделок, период, справочники, категории, статусы. Используй для вопросов 'что вообще есть в данных'.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_deal",
            "description": "Карточка одной сделки по её номеру (Номер сделки).",
            "parameters": {
                "type": "object",
                "properties": {"deal_no": {"type": "integer", "description": "Номер сделки"}},
                "required": ["deal_no"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_deals",
            "description": "Поиск сделок по фильтрам. Возвращает до limit записей (свежие сверху) и общее число совпадений.",
            "parameters": {
                "type": "object",
                "properties": _props(
                    "product", "seller", "buyer", "region", "status",
                    "contract_type", "contract_form", "auction_type", "tnved",
                    "date_from", "date_to",
                    limit={"type": "integer", "description": "Сколько записей вернуть (1-100)", "default": 20},
                ),
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aggregate_deals",
            "description": "Главный инструмент для 'топ', 'сколько', 'по регионам/годам/товарам/компаниям'. Агрегирует сделки по group_by и метрике.",
            "parameters": {
                "type": "object",
                "properties": _props(
                    "product", "seller", "buyer", "region", "status",
                    "contract_type", "contract_form", "auction_type", "tnved",
                    "date_from", "date_to",
                    group_by={
                        "type": "string",
                        "description": "Разрез: product, product_category, product_group, tnved, industry, region, unit, month, year, status, contract_type, contract_form, auction_type, currency, seller, seller_inn, buyer, buyer_inn, seller_broker, buyer_broker",
                    },
                    metric={
                        "type": "string",
                        "description": "Метрика: amount_uzs (сумма в сумах, по умолчанию), volume (объём), deals (число сделок), amount (в валюте контракта), delivered, avg_price (сум/единица), avg_deal_amount",
                        "default": "amount_uzs",
                    },
                    product_category={"type": "string", "description": "Категория товара, например 'Сельскохозяйственная продукция'"},
                    currency={"type": "string", "enum": ["Узбекский сум", "Доллар США"]},
                    unit={"type": "string", "description": "Единица измерения: тонна, килограмм, литр, штука, куб.метр"},
                    limit={"type": "integer", "default": 15, "description": "Топ-N (1-100)"},
                ),
                "required": ["group_by"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "product_stats",
            "description": "Сводка по конкретному товару: сделки, объём, сумма, средняя/мин/макс цена, топ-5 продавцов и покупателей, статусы.",
            "parameters": {
                "type": "object",
                "properties": _props("product", "region", "contract_type", "date_from", "date_to"),
                "required": ["product"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "price_dynamics",
            "description": "Динамика средневзвешенной цены (сум за единицу), объёма и курса USD по дням/неделям/месяцам/годам.",
            "parameters": {
                "type": "object",
                "properties": _props("product", "tnved", "region", "contract_type", "date_from", "date_to",
                                     granularity={"type": "string", "enum": ["day", "week", "month", "year"],
                                                  "default": "month"}),
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_contract",
            "description": "Карточка контракта по номеру + статистика сделок по нему.",
            "parameters": {
                "type": "object",
                "properties": {"contract_no": {"type": "integer"}},
                "required": ["contract_no"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_contracts",
            "description": "Поиск карточек контрактов по товару, коду ТН ВЭД, региону, продавцу, валюте, форме.",
            "parameters": {
                "type": "object",
                "properties": _props("product", "tnved", "region", "seller", "currency", "contract_form",
                                     limit={"type": "integer", "default": 20}),
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_company",
            "description": "Найти компанию по ИНН или названию. Возвращает карточку и число сделок как продавца/покупателя.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "ИНН (9 цифр) или часть названия"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "company_stats",
            "description": "Подробная статистика компании по ИНН: обороты, топ товаров и контрагентов как продавца и как покупателя.",
            "parameters": {
                "type": "object",
                "properties": _props("date_from", "date_to", inn={"type": "string", "description": "ИНН компании, 9 цифр"}),
                "required": ["inn"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_broker",
            "description": "Брокерская контора по коду (BK001), названию или ИНН + статистика сделок.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "usd_rate",
            "description": "Курс доллара на дату/период (сум за 1 USD).",
            "parameters": {
                "type": "object",
                "properties": _props("date_from", "date_to"),
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sql_query",
            "description": "Fallback для сложных вопросов, не покрытых другими инструментами. Только один SELECT по таблицам deals, deals_enriched, deals_monthly, contracts, participants, brokers, usd_rates. Обязателен LIMIT.",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string", "description": "SELECT-запрос на диалекте DuckDB"}},
                "required": ["sql"],
            },
        },
    },
]

TOOL_FUNCS: dict[str, Callable[..., dict[str, Any]]] = {
    "dataset_overview": dataset_overview,
    "get_deal": get_deal,
    "search_deals": search_deals,
    "aggregate_deals": aggregate_deals,
    "product_stats": product_stats,
    "price_dynamics": price_dynamics,
    "get_contract": get_contract,
    "find_contracts": find_contracts,
    "get_company": get_company,
    "company_stats": company_stats,
    "get_broker": get_broker,
    "usd_rate": usd_rate,
    "sql_query": sql_query,
}


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    func = TOOL_FUNCS.get(name)
    if func is None:
        return {"ok": False, "error": f"Неизвестный инструмент: {name}"}
    try:
        result = func(**args)
        return {"ok": True, **result}
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    except TypeError as exc:
        return {"ok": False, "error": f"Неверные аргументы: {exc}"}
    except Exception as exc:  # не роняем бота из-за одного запроса
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
