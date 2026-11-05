# Деплой: Yandex Cloud VM (ядро) + Render (бот)

```
Telegram ──polling──► Render Background Worker (bot.py)
                            │  POST /api/ask  + X-API-Key
                            ▼
                     Yandex Cloud VM (docker compose)
                     FastAPI + Agent + DuckDB (21 МБ)
                            │
                            ▼
                     LLM (Ollama Cloud / OpenRouter)
```

- **Render** знает только `BOT_TOKEN`, `AGENT_API_URL`, `AGENT_API_TOKEN`.
- **Yandex Cloud** знает `LLM_*` и `AGENT_API_TOKEN`; CSV и DuckDB есть только здесь.

---

## 1. Yandex Cloud: ВМ с Docker

### 1.1. Создать ВМ

1. Консоль Yandex Cloud → **Compute Cloud** → **Создать ВМ**.
2. Ubuntu 24.04 LTS, 2 vCPU / 2 ГБ RAM (минимум), публичный IP.
3. В группе безопасности открыть порты:
   - `22/tcp` — SSH;
   - `8000/tcp` — API (или `80/443`, если ставите reverse proxy).
4. Подключиться:

```bash
ssh ubuntu@<PUBLIC_IP>
```

### 1.2. Установить Docker

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker
docker compose version
```

### 1.3. Забрать проект

```bash
git clone <REPO_URL> uzex
cd uzex
```

> Если репозитория ещё нет — скопируйте проект с локальной машины:
> `rsync -av --exclude .venv --exclude .git --exclude data /home/ex1ndle/uzex/ ubuntu@<IP>:~/uzex/`
> (CSV `uzex_spot_data/` обязательны — база собирается из них в образе.)

### 1.4. Создать `.env`

```bash
cp .env.example .env
openssl rand -hex 32          # сгенерировать AGENT_API_TOKEN
nano .env
```

Минимальный `.env` на ВМ:

```dotenv
# LLM (ключ Ollama Cloud)
LLM_BASE_URL=https://ollama.com/v1
LLM_API_KEY=<ollama-key>
LLM_MODELS=gemma4:31b-cloud
LLM_TIMEOUT=120
LLM_TEMPERATURE=0

# Общий секрет с Render
AGENT_API_TOKEN=<длинная случайная строка>

# BOT_TOKEN на ВМ не нужен
```

### 1.5. Собрать и запустить

```bash
docker compose up -d --build     # ETL ~10–30 с, образ ~250 МБ
docker compose logs -f api
```

Проверка:

```bash
curl -s http://127.0.0.1:8000/healthz
# {"status":"ok","database":"ok"}

curl -s -X POST http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $AGENT_API_TOKEN" \
  -d '{"question":"Покажи сделку №100001"}'
```

Снаружи: `http://<PUBLIC_IP>:8000/healthz`, веб-чат — `http://<PUBLIC_IP>:8000/`.

### 1.6. HTTPS (рекомендуется)

Без TLS токен `X-API-Key` и вопросы идут по сети открытым текстом. Вариант с
Caddy и доменом (A-запись → IP ВМ):

```bash
sudo apt-get install -y caddy
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
api.example.com {
    reverse_proxy 127.0.0.1:8000
}
EOF
sudo systemctl restart caddy
```

Тогда `AGENT_API_URL=https://api.example.com`, а порт `8000` можно закрыть
в группе безопасности.

---

## 2. Render: Background Worker

1. Запушьте репозиторий в GitHub/GitLab.
2. Render Dashboard → **New** → **Blueprint** → выбрать репозиторий.
   Render прочитает `render.yaml` и создаст worker `uzex-telegram-bot`.
3. В **Environment** задать секреты:
   - `BOT_TOKEN` — токен из BotFather;
   - `AGENT_API_URL` — `http://<PUBLIC_IP>:8000` или `https://api.example.com`;
   - `AGENT_API_TOKEN` — тот же секрет, что в `.env` на ВМ.
4. **Deploy**. В логах должно быть:

```
Режим бота: удалённый API http://<IP>:8000
Бот запущен (polling)
Run polling for bot @uzex_prototype_bot
```

Проверка: написать боту `/start`, затем вопрос на RU и UZ.

---

## 3. Обновление

**Ядро (YC):**

```bash
cd ~/uzex && git pull
docker compose up -d --build
```

**Бот (Render):** перезапускается автоматически при пуше (`autoDeploy: true`).

---

## 4. Диагностика

| Симптом | Что проверить |
|---|---|
| Бот молчит | логи Render: `BOT_TOKEN`, запущен ли worker |
| `API отклонил ключ (401)` | `AGENT_API_TOKEN` на Render и на ВМ совпадают |
| `API недоступен` / таймаут | `curl http://<IP>:8000/healthz` с ВМ и снаружи, порт в security group |
| `database: error` | `docker compose logs api`; пересобрать: `docker compose up -d --build` |
| Ответ «LLM не настроен» | в `.env` на ВМ пуст `LLM_API_KEY`, перезапустить контейнер |
| Медленно (>30 с) | `LLM_TIMEOUT`, доступность `api.ollama.com` с ВМ |

## 5. Стоимость

- ВМ 2 vCPU / 2 ГБ — по тарифу Yandex Cloud (подписка пользователя).
- Render Background Worker — план `starter` (free-план worker'ы не поддерживает).
- LLM — Ollama Cloud, ~$0.001–0.002 за вопрос.
