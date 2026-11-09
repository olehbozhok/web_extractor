# Архітектура: web_extractor

Це стартовий дизайн. Його змінюють лише разом із оновленням цього файлу в тому самому коміті.

## 1. Компоненти

```
 агенти (MCP, streamable HTTP)         людина (браузер)
            │                                  │
            ▼                                  ▼
   ┌─────────────────── core (контейнер 1) ───────────────────┐
   │  mcp_api        web (Jinja2 + HTMX)                      │
   │       └────────┬────────┘                                │
   │            service (конвеєр)                             │
   │   нормалізація → SSRF → кеш → single-flight → fetch →    │
   │   extract → збереження → подія                           │
   │       │             │              │                     │
   │   Fetcher       Extractor        storage (SQLite WAL)    │
   │   (протокол)    (протокол)       cache_pages, events     │
   └───────┬───────────────────────────────────────────────────┘
           │ REST (внутрішня мережа compose)
           ▼
   ┌── browser (етап 3) ────────────────┐
   │ Crawl4AI, пул браузерів            │
   │ POST /crawl → html, status, ...    │
   │ вбудований egress-проксі:          │
   │ закріплення IP, лише глобальні     │──► інтернет
   └────────────────────────────────────┘
```

Сервіси: ядро (власний код) і браузер (готовий образ Crawl4AI). Trafilatura — чиста функція над HTML без стану, тому живе модулем у ядрі. Браузер винесений, бо він важкий, нестабільний і потребує окремого sandbox; власний Playwright-сервіс не пишемо, бо Crawl4AI закриває цю роль (`docs/spikes/01_crawl4ai.md`). Екстрактор працює з HTML незалежно від того, який фетчер його отримав. Інтерфейс `Extractor` дозволяє замінити реалізацію або пізніше винести її в контейнер без змін конвеєра.

## 2. Шари й напрямок залежностей

```
domain  ←  fetchers, extractors, storage  ←  service  ←  mcp_api, web
```

- `domain` не імпортує нічого з решти пакета й не виконує I/O: типи (`NormalizedUrl`, `FetchOutcome`, `PageRecord`, ...), нормалізація URL, правила SSRF над адресами, порівняння TTL.
- `fetchers`, `extractors`, `storage` реалізують протоколи, оголошені в `domain` або `service`. Вони не знають одне про одного.
- `service` складає конвеєр і отримує всі залежності через конструктор (ін'єкція, без глобальних синглтонів).
- `mcp_api` і `web` — тонкі адаптери: розбирають вхід у типи, викликають `service`, форматують відповідь. Бізнес-логіки в них немає.
- Збирання залежностей відбувається в одному місці (composition root).

## 3. Ключові типи (орієнтир, не обмеження)

```python
type Status = Literal["ok", "blocked", "needs_browser", "not_found", "timeout", "error"]

class NormalizedUrl:            # newtype навколо перевіреного рядка; створюється лише парсером
class AgentId:                  # непорожній, обмеженої довжини, дозволений набір символів
class FetchRequest:             # url: NormalizedUrl, fresh: bool, render: RenderMode, include: Include, agent: AgentId, task: TaskId | None

class Fetched:                  # успішне завантаження: final_url, http_status, headers, body: bytes, fetcher
class FetchFailure:             # blocked | not_found | timeout | too_large | network | ... (дискримінований union)
type FetchResult = Fetched | FetchFailure

class Extracted:                # text: NonEmptyStr, metadata: PageMetadata
class ExtractionFailure:        # empty | needs_js | parse_error
type ExtractResult = Extracted | ExtractionFailure
```

Помилки виконання — це значення в union (`Result`-подібні), а не винятки, якщо їх очікує викликач. Винятки лишаються для порушення інваріантів і непередбачених збоїв інфраструктури.

## 4. Single-flight

Мапа `key -> asyncio.Task[FetchOutcome]` під асинхронним замком. Запит із наявним ключем чекає на існуючу задачу через `asyncio.shield`, щоб скасування одного очікувача не скасувало спільну роботу. Задача при завершенні (успіх, помилка, скасування) завжди видаляється з мапи в `finally`. `fresh=true` не приєднується до вже запущеної задачі, яка стартувала до його надходження, якщо вона не `fresh`; це правило описати тестом.

## 5. Ліміт на домен

Для кожного хоста: `asyncio.Semaphore(max_concurrent)` і мінімальний інтервал між стартами запитів. Конфігурація за замовчуванням плюс перевизначення для доменів. Очікування в черзі враховується в `duration_ms` окремо від мережевого часу (поле `queue_ms`).

## 6. Схема БД (стартова)

```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE cache_pages (
  url_key        TEXT PRIMARY KEY,      -- нормалізований URL
  final_url      TEXT NOT NULL,
  status         TEXT NOT NULL,
  http_status    INTEGER,
  fetcher        TEXT NOT NULL,
  fetched_at     TEXT NOT NULL,         -- UTC ISO 8601
  expires_at     TEXT NOT NULL,
  content_sha256 TEXT,
  html           BLOB,                  -- може бути NULL для помилкових записів
  text           TEXT,
  metadata_json  TEXT NOT NULL,
  error_kind     TEXT,
  error_message  TEXT,
  text_format    TEXT,                  -- формат збереженого тексту (міграція 002)
  content_type   TEXT                   -- заявлений медіатип відповіді (міграція 003)
);

CREATE TABLE cursors (            -- короткі курсори пагінації для MCP (docs/03, 3.3)
  id             TEXT PRIMARY KEY,      -- 6 символів, алфавіт без двозначних
  url_key        TEXT NOT NULL,
  text_sha256    TEXT NOT NULL,         -- версія тексту, для якої створений курсор
  offset         INTEGER NOT NULL,
  max_chars      INTEGER NOT NULL,
  format         TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  expires_at     TEXT NOT NULL,
  UNIQUE (url_key, text_sha256, offset, max_chars, format)   -- один і той самий стан дає той самий курсор
);

CREATE TABLE events (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  ts             TEXT NOT NULL,
  agent          TEXT NOT NULL,
  task           TEXT,
  mcp_session_id TEXT,                    -- NULL, якщо транспорт не надає сесії (нова специфікація MCP її не має)
  request_id     TEXT NOT NULL,
  tool           TEXT NOT NULL,
  url            TEXT NOT NULL,
  final_url      TEXT,
  cache_hit      INTEGER NOT NULL,
  fresh          INTEGER NOT NULL,
  fetcher        TEXT,
  status         TEXT NOT NULL,
  http_status    INTEGER,
  duration_ms    INTEGER NOT NULL,
  queue_ms       INTEGER NOT NULL,
  bytes          INTEGER,
  content_sha256 TEXT,
  error_kind     TEXT
);
CREATE INDEX events_ts ON events(ts);
CREATE INDEX events_agent_ts ON events(agent, ts);
CREATE INDEX events_status_ts ON events(status, ts);
```

Усі запити параметризовані. Запис в `events` не блокує відповідь агенту довше, ніж потрібно для одного короткого запису, і не губить подію при завершенні процесу (graceful shutdown дописує чергу).

## 7. Контракт браузерного конектора (етап 3)

Конектор — це REST Crawl4AI, а не наш власний API. Ядро викликає `POST /crawl` з токеном у `Authorization: Bearer ...`:

```
POST /crawl
{ "urls": [url], "browser_config": {...}, "crawler_config": { "cache_mode": "bypass", ... } }
```

Кеш Crawl4AI вимкнений (`cache_mode: "bypass"`): єдиний кеш — наш, щоб був один набір правил TTL і `fresh`.

Поля відповіді `results[0]`, що використовуються (перевірено в `docs/spikes/01_crawl4ai.md`):

| Поле Crawl4AI | У що відображається |
|---|---|
| `html` | сирий HTML для кешу й `content_sha256` |
| `redirected_url` | `final_url` |
| `redirected_status_code` | `http_status` (не `status_code`, воно містить код першої відповіді) |
| `response_headers` | заголовки відповіді |
| `success`, `error_message`, `crawl_stats.proxies_used[].blocked` | вибір між `ok`, `blocked`, `error` |
| `metadata` | початкові метадані (заголовок, опис); автор і дату бере наш екстрактор |

`BrowserFetcher` — адаптер: він парсить відповідь у типи `domain` (розділ 3) і повертає `FetchResult`. Структура відповіді Crawl4AI не виходить за межі цього модуля. Контейнер нічого не знає про агентів і не пише в нашу БД.

### 7.1. Мережева топологія compose

```
 networks:
   core_net     # internal: true; ядро <-> browser (REST)
   egress_net   # звичайна; browser -> інтернет
```

- `core`: `core_net`; його порт публікується лише на `127.0.0.1` (через окрему мережу для публікації, бо `internal`-мережа не дозволяє публікацію портів).
- `browser`: `core_net` і `egress_net`. Порт Crawl4AI назовні не публікується. SSRF-захист забезпечує вбудований egress-проксі Crawl4AI (закріплення IP, лише глобальні адреси), а ядро додатково перевіряє `final_url` (`docs/spikes/02_egress_isolation.md`).
- Томи: БД ядра; кеш браузера не персистентний.
- `browser`: `restart: unless-stopped`, `shm_size: 1g`, `mem_limit`, образ із закріпленим тегом.
- **Healthcheck браузера** виконує `raw:`-краул із `wait_for`, тобто реально використовує браузер. `/health` Crawl4AI не відображає смерть Chromium. При невдачі скрипт завершує PID 1, і `restart: unless-stopped` перезапускає контейнер без docker-сокета (від падіння до відновлення близько 20 с). Власні запити ядра в цей час отримують `error`, а не зависають: кожен запит має таймаут.
- `core` не залежить жорстко від готовності `browser` (`depends_on` без `service_healthy`): падіння браузера не блокує старт ядра. Готовність браузера відображається в `/readyz` окремим пунктом.
- Дія `restart_browser` у моніторі Crawl4AI не використовується: у спайку 02 вона зависала й погіршувала стан.
