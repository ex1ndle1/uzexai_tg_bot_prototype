# UZEX AI Agent — реестр биржевых сделок

AI-агент для Telegram, отвечающий на вопросы о биржевых сделках **на русском и
узбекском языках** строго по данным реестра `uzex_spot_data/`.

Официальное задание отбора: [hackathon.uzex.uz/saralash](https://hackathon.uzex.uz/saralash/).

---

## Что умеет

- сделка по номеру, контракт по номеру, компания по ИНН/названию, брокер по коду;
- «сколько / топ-N / по регионам / по годам / по товарам / по компаниям» —
  суммы в сумах, объёмы, число сделок, средние цены;
- динамика цен по месяцам и сравнение с курсом USD;
- экспорт vs внутренний рынок, статусы сделок, доля аннулированных;
- честный ответ «в реестре таких данных нет», если данных действительно нет;
- язык ответа фиксируется по языку вопроса (RU/UZ), включая строку источника;
  узбекские названия товаров переводятся к терминам реестра (`paxta → хлопок`).

Данные вымышленные (так задумано организаторами): 500 000 сделок за
03.01.2022–30.10.2026, 6 616 контрактов, 14 392 компании, 120 брокеров,
курс USD за каждый день.

## Быстрый старт

```bash
cd /home/ex1ndle/uzex
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 1) CSV -> DuckDB (≈2 секунды)
.venv/bin/python -m agent.etl

# 2) ключи
cp .env.example .env
#   BOT_TOKEN=...            (BotFather)
#   LLM_API_KEY=...          (Ollama Cloud, модель gemma4:31b-cloud — см. .env.example)
#   либо бесплатный OpenRouter (OPENROUTER_API_KEY=... во втором блоке)



# 3) проверка без Telegram
.venv/bin/python -m agent.cli "Топ-5 товаров по сумме сделок в 2024"

# 4) бот
.venv/bin/python bot.py
```

Без ключа LLM работает offline-режим: точные ответы на вопросы
«сделка №…», «ИНН …», «курс на дату…» и общая справка по реестру.

## LLM-провайдеры

| Провайдер | Конфиг | Комментарий |
|---|---|---|
| **Ollama Cloud** (основной) | `LLM_BASE_URL=https://ollama.com/v1`, `LLM_MODELS=gemma4:31b-cloud` | Gemma 4 31B dense, 256K контекст, нативный function calling, ~$0.001–0.002 за вопрос |
| **OpenRouter** (резерв) | `LLM_BASE_URL=https://openrouter.ai/api/v1`, `OPENROUTER_API_KEY=...` | Бесплатные модели (`deepseek-chat-v3.1:free`, `gemma-4-31b-it:free`), лимиты free-тира |

`agent/llm.py` перебирает модели из `LLM_MODELS` по порядку при ошибках/429,
чистит thinking-канал Gemma 4 (`<|channel>thought … <channel|>`) из ответов и
не сохраняет размышления в историю диалога.

## Архитектура

```
Telegram (aiogram) ──► Agent (orchestrator)
                          │  системный промпт RU+UZ
                          │  function calling, до 5 шагов
                          ▼
                 13 инструментов (deterministic SQL)
                          │
                          ▼
                 DuckDB: deals / contracts / participants /
                         brokers / usd_rates (+ views)
```

- **ETL** (`agent/etl.py`): CSV → DuckDB. Числа вида `304 547 938.4`,
  даты `ДД.ММ.ГГГГ`, пустые значения, варианты написания товара
  (`Хлопок-сырец` / `ХЛОПОК СЫРЕЦ`) нормализуются; товары склеиваются по
  `product_key`, для показа выбирается самое частое написание.
- **Инструменты** (`agent/tools.py`): каждый — параметризованный SQL-запрос.
  LLM не имеет доступа к таблицам напрямую и не может «придумать» строку.
- **Fallback** `sql_query`: один `SELECT` только по 7 разрешённым таблицам,
  без DDL/DML и чтения файлов, с принудительным `LIMIT 100`.
- **LLM** (`agent/llm.py`): Ollama Cloud или OpenRouter, температура 0,
  перебор моделей при 429/5xx.

## Почему ответы не выдумываются

1. Любое число в ответе берётся из JSON-результата инструмента.
2. `found=false` / пустой результат → агент обязан сказать «данных нет».
3. Вопрос вне реестра (прогнозы, новости) → отказ со ссылкой на границы данных.
4. `temperature=0`, короткий системный промпт с явными правилами и примерами.
5. Детерминированные SQL-агрегаты вместо «спроси у модели, сколько было сделок».

6. Поиск компаний и брокеров по названию работает в любом алфавите:
   в ETL строится латинская транслитерация имени, поэтому «РУКУШО ФЕРМЕР»
   находит `RUKUSHO‘ FERMER MCHJ`, и наоборот.

## Проверка качества

```bash
.venv/bin/python -m tests.eval --tools   # инструменты vs эталонный SQL (13 проверок)
.venv/bin/python -m tests.eval           # + прогон вопросов через LLM (8 проверок)
.venv/bin/python -m tests.test_api       # HTTP-ядро: auth, healthz, offline-ответ (5 проверок)
```

## Структура

```
agent/
  etl.py           CSV → DuckDB + нормализация
  textutil.py      нормализация и транслитерация названий
  db.py            подключение, JSON-сериализация
  tools.py         13 инструментов + JSON-схемы function calling
  prompts.py       системный промпт (RU + UZ)
  llm.py           клиент Ollama Cloud / OpenRouter, fallback моделей
  orchestrator.py  цикл tool-calling, offline-режим
  api.py           FastAPI-ядро: /api/ask, /healthz, веб-чат
  client.py        клиент ядра для тонкого бота
  cli.py           проверка из терминала
bot.py             Telegram-бот (aiogram 3; локально или через API)
Dockerfile.api     образ ядра (ETL запекается в образ)
docker-compose.yml запуск ядра на ВМ
render.yaml        Blueprint для Background Worker на Render
deploy/yc.md       пошаговый деплой YC + Render
tests/eval.py      проверки качества
tests/test_api.py  проверки HTTP-ядра
data/uzex.duckdb   собранная база (генерируется ETL)
```

## Деплой

Схема: **бот на Render** (polling, только aiogram) → HTTPS + `X-API-Key` →
**ядро на Yandex Cloud VM** (FastAPI + Agent + DuckDB + LLM).

- На Render: `BOT_TOKEN`, `AGENT_API_URL`, `AGENT_API_TOKEN`
  (`render.yaml`, план `starter` — worker'ы не входят в free-тир).
- На ВМ: `LLM_*`, `AGENT_API_TOKEN`; база собирается из CSV в образ.
- Локально без `AGENT_API_URL` бот работает как раньше — с полным агентом.

Пошаговая инструкция: [`deploy/yc.md`](deploy/yc.md).

```bash
# ядро на ВМ
docker compose up -d --build
curl -s http://127.0.0.1:8000/healthz

# бот на Render — автоматически из render.yaml
```

## Ограничения

- Реестр вымышленный; реальные компании/цены искать бессмысленно.
- Данные заканчиваются 30.10.2026 — прогнозов нет.
- Названия компаний в разных системах письма (кириллица/латиница), поэтому
  поиск по названию — подстрока; надёжнее искать по ИНН.
- Бесплатные модели OpenRouter имеют лимиты запросов; при 429 клиент
  автоматически переключается на следующую модель.

---

## O‘zbekcha qisqacha

`uzex_spot_data/` reyestri asosida rus va o‘zbek tillarida javob beradigan
Telegram-agent. Ma’lumotlar DuckDB’ga yuklanadi, LLM faqat 13 ta tayyor
SQL-instrument natijalari asosida javob yozadi — raqamlar, nomlar va STIR
o‘ylab topilmaydi. Ma’lumot bo‘lmasa, agent buni ochiq aytadi.

Ishga tushirish: `python -m agent.etl` → `.env` faylida `BOT_TOKEN` va
`OPENROUTER_API_KEY` → `python bot.py`.
