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
           │ HTTP (внутрішня мережа compose)
           ▼
   ┌── browser (контейнер 2, етап 3) ──┐
   │ Playwright, пул контекстів        │
   │ POST /render → html, headers, ... │
   └───────────────────────────────────┘
```

Два контейнери, а не три: Trafilatura — чиста функція над HTML без стану, тому живе модулем у ядрі. Браузер винесений, бо він важкий, нестабільний і потребує окремого sandbox. Екстрактор працює з HTML незалежно від того, який фетчер його отримав. Інтерфейс `Extractor` дозволяє замінити реалізацію або пізніше винести її в контейнер без змін конвеєра.

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
  error_message  TEXT
);

CREATE TABLE events (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  ts             TEXT NOT NULL,
  agent          TEXT NOT NULL,
  task           TEXT,
  mcp_session_id TEXT,
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

```
POST /render
{ "url": str, "wait_until": "load"|"domcontentloaded"|"networkidle",
  "wait_for_selector": str | null, "timeout_ms": int }

200 { "status": "ok", "final_url": str, "http_status": int, "html": str,
      "headers": {str: str}, "redirect_chain": [str], "page_id": str, "timings": {...} }
4xx/5xx { "error": { "kind": str, "message": str } }
```

Контейнер нічого не кешує, не знає про агентів і не пише в БД. Усі підзапити сторінки проходять ту саму перевірку SSRF.
