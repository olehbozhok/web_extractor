# web_extractor

Локальний спільний сервіс для LLM-агентів: завантажує веб-сторінки, витягує з них чистий текст (Trafilatura), кешує результат, об'єднує одночасні запити до одного ресурсу й веде журнал усіх дій із вебмордою для статистики та налагодження. Агенти звертаються до нього через MCP (streamable HTTP).

Статус: етап 1 (ядро) реалізований — MCP-інтерфейс з чотирма інструментами, кеш, SSRF-захист, single-flight, ліміти на домен, журнал подій у SQLite (WAL), Docker Compose. Вебморда (Dashboard, Events, Agents, Cache) і браузерний конектор Crawl4AI реалізовані, але браузерний контейнер ще не перевірявся на реальному трафіку.

Сторінки за замовчуванням рендерить браузер (`render=browser`). PDF і інші завантаження читаються лише з `render=static`. Якщо браузер не впорався, помилка містить `retry_render` — режим, з яким варто повторити запит. Без браузерного контейнера (не задані `WX_BROWSER_BASE_URL` і `WX_BROWSER_TOKEN`) запити без `render` повертають `browser_unavailable` з порадою `render=static`.

## Швидкий старт (Docker Compose)

Перед першим запуском потрібен токен `CRAWL4AI_API_TOKEN`. Це не платний ключ, а спільний пароль між двома локальними контейнерами (ядро й браузер): його вигадуєте ви самі. Один раз створіть `.env` (він у `.gitignore`; шаблон у `.env.example`):

```bash
echo "CRAWL4AI_API_TOKEN=$(python -c 'import secrets;print(secrets.token_hex(24))')" > .env
```

```bash
docker compose up --build -d
curl http://127.0.0.1:8321/healthz   # {"status":"ok"}
curl http://127.0.0.1:8321/readyz    # {"database":true}
```

Оновлення після змін у коді одним кроком: `./reinstall.sh` (зупиняє стек, перезбирає образ ядра, піднімає все знову й чекає `/readyz`; створює `.env` з випадковим токеном, якщо його немає). Кеш і журнал зберігаються; `./reinstall.sh --wipe-data` видаляє і їх.

Сервіс слухає лише `127.0.0.1` на хості (порт 8321); кеш і журнал зберігаються у docker volume `core-data` і переживають перезапуск.

## Підключення MCP-клієнта

Приклад для `.mcp.json` (Claude Code) або еквівалента:

```json
{
  "mcpServers": {
    "web_extractor": {
      "type": "http",
      "url": "http://127.0.0.1:8321/mcp"
    }
  }
}
```

Три інструменти для агентів — `fetch_page` (текст сторінки порціями), `read_more` (продовження за курсором), `find_in_page` (перевірка цитат із позначкою дослівності) — і `check_urls` (живість списку URL одним викликом). Повний дизайн інтерфейсу — `docs/03_mcp_interface.md`. Рекомендується задавати у клієнті постійний заголовок `X-Agent-Id: <роль агента>` для читабельного журналу.

## Локальна розробка

```bash
uv sync                 # залежності з uv.lock
bash check.sh           # ruff check + format --check + mypy strict + pytest
uv run python -m web_extractor   # запуск без Docker (WX_ env, див. config.py)
```

Конфігурація — змінні середовища з префіксом `WX_` (порт, шлях до БД, TTL кешу, таймаути, ліміти доменів тощо); повний список із межами значень — `src/web_extractor/config.py`. Невалідна конфігурація зупиняє старт із повідомленням.

## Структура

| Шлях | Зміст |
|---|---|
| `src/web_extractor/domain/` | чисті типи й правила без I/O: `NormalizedUrl`, SSRF-правила, TTL |
| `src/web_extractor/storage/` | SQLite (WAL), міграції, репозиторії, статистика |
| `src/web_extractor/fetchers/` | `HttpFetcher` на httpx з pinned-DNS SSRF-захистом |
| `src/web_extractor/extractors/` | `TrafilaturaExtractor` (у робочому потоці) |
| `src/web_extractor/service/` | конвеєр: кеш, single-flight, ліміти доменів, курсори, пошук |
| `src/web_extractor/mcp_api/` | MCP-інструменти, форматування відповідей, composition root |
| `docker/core.Dockerfile` | багатоетапний образ, non-root |
| `compose.yaml` | стек етапу 1; браузерний сервіс додається на етапі 3 |

## Документи

| Файл | Зміст |
|---|---|
| [`AGENTS.md`](AGENTS.md) | правила роботи агента: порядок, типізація, помилки, тести, безпека |
| [`docs/01_requirements.md`](docs/01_requirements.md) | навіщо, межі, сценарії, функціональні й нефункціональні вимоги, етапи, стек |
| [`docs/02_architecture.md`](docs/02_architecture.md) | компоненти, шари, ключові типи, схема БД, контракт браузерного конектора |
| [`docs/03_mcp_interface.md`](docs/03_mcp_interface.md) | аналіз і дизайн MCP-інтерфейсу для ШІ-агентів: інструменти, формат відповіді, помилки, ідентифікація |
| [`docs/04_plan.md`](docs/04_plan.md) | план реалізації за віхами, залежності, ризики |
| [`docs/spikes/`](docs/spikes/01_crawl4ai.md) | результати досліджень: Crawl4AI ([01](docs/spikes/01_crawl4ai.md)), ізоляція ([02](docs/spikes/02_egress_isolation.md)), MCP SDK 2.x ([03](docs/spikes/03_mcp_sdk.md)) |

## Етапи

1. Ядро: HTTP-фетчер, Trafilatura, кеш, single-flight, SSRF-захист, журнал, MCP. ✅
2. Вебморда: статистика, події, перегляд кешу.
3. Браузерний конектор на базі Crawl4AI за egress-проксі (Docker Compose).
4. Оптимізація за даними журналу.
