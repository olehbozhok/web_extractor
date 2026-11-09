# Spike 02: ізоляція браузерного конектора, навантаження, відновлення

Дата: 2026-11-09. Образ `unclecode/crawl4ai:latest`, сервер 0.9.5. Стенд працював у Docker Compose, після спайку знищений (`docker compose down -v`). Закриває неперевірені пункти зі спостереження 8 у `docs/spikes/01_crawl4ai.md` і спростовує початковий дизайн мережевої ізоляції.

## Стенд

Мережі compose:

| Мережа | Параметри | Призначення |
|---|---|---|
| `core_net` | `internal: true` | клієнт (probe, у проєкті — ядро) до Crawl4AI; виходу назовні немає |
| `pub_net` | `93.184.216.0/24` | імітація інтернету: редиректор `93.184.216.10:8080` з глобальною адресою |
| `priv_net` | `172.30.0.0/24` | імітація приватної інфраструктури, доступної з контейнера браузера: `internal-target` `172.30.0.20:8080` |

Crawl4AI підключений до всіх трьох. `redirector` віддає 302 на довільну адресу, сторінку з `img` і `fetch` до приватних цілей, сторінку з `iframe`. `internal-target` логує кожен запит: **будь-який запис у його журналі означав би провал ізоляції**.

## Результати

| # | Перевірка | Результат |
|---|---|---|
| 1 | Реальний публічний сайт (`https://example.com/`) | `success=true`, 200 |
| 2 | Редірект на приватний IP-літерал `172.30.0.20` | кінцевий код 403, `internal-target` не отримав запиту |
| 3 | Редірект на приватне ім'я (`internal-target`, Docker DNS) | 403, цілі не торкнулися |
| 4 | Редірект на `169.254.169.254`, `127.0.0.1:11235`, сервіс `crawl4ai:11235` | усі 403, цілі не торкнулися |
| 5 | Підзапити сторінки (`<img>`, `fetch`) до `internal-target` і `169.254.169.254` (з `capture_network_requests`) | браузер спробував, запити завершені `403` / `net::ERR_FAILED`, `internal-target` не отримав жодного запиту |
| 6 | Публічні імена, що резолвляться в loopback (`localtest.me`, `127.0.0.1.nip.io`) | відхилені до запуску браузера (HTTP 400, `URL blocked`) |
| 7 | Контроль: редірект на дозволену глобальну адресу | пройшов, кінцевий код 200, вміст отримано |
| 8 | `iframe` зі стандартною конфігурацією | вміст iframe у `html` відсутній, хоча браузер його завантажив |
| 9 | `iframe` з `process_iframes: true` | вміст iframe є в `html` |
| 10 | Навантаження: 10, 30, 60 паралельних запитів до різних URL одного джерела | помилок транспорту 0; для 60: p50 3.4 с, p95 3.8 с; пам'ять контейнера 0.6–0.7 ГБ, у спокої ~0.6 ГБ, CPU до 460% (кілька ядер) |
| 11 | Падіння Chromium (`pkill -9` усередині контейнера) | усі наступні запити (понад 120 с) повертають `success=false` з помилкою Playwright, `/health` при цьому `ok`; самовідновлення відсутнє |
| 12 | Дія `/monitor/actions/restart_browser` на `sig` браузера | виклик завис (таймаут 60 с); після нього запити зависали до таймауту, стан гірший за початковий |
| 13 | Зупинка й старт контейнера | з боку клієнта помилка DNS/з'єднання на час зупинки; після старту (близько 10 с) сервіс працює, запити проходять |
| 14 | Healthcheck із `raw:`-краулом без `wait_for` | лишався `healthy` при мертвому браузері: `raw:` не використовує браузер |
| 15 | Healthcheck із `raw:`-краулом і `wait_for: css:p`, при невдачі завершення PID 1, `restart: unless-stopped` | від загибелі Chromium до `unhealthy`/перезапуску ~11 с, до `healthy` ~21 с, наступні запити успішні |
| 16 | Моніторинг: запити, відхилені перевіркою SSRF до запуску браузера | залишаються в `active` з наростаючим `elapsed` (підтверджено двічі) |

## Висновки, що змінюють дизайн

1. **Вбудований egress-проксі Crawl4AI закриває редіректи й підзапити.** Із коду (`egress_proxy.py`): Chromium спрямований на локальний проксі, який резолвить DNS один раз, відкидає не-глобальні адреси й підключається до закріпленого IP, тож DNS rebinding не проходить. Експерименти 2–6 це підтверджують. Окремий egress-проксі (squid) і `internal`-мережа для браузера **не потрібні** і навіть шкодять: перевірка URL у Crawl4AI резолвить DNS у самому контейнері, а в мережі без DNS вона блокує навіть публічні сайти (спостерігалося на першій, відкинутій версії стенда). Також Crawl4AI відхиляє `proxy_config` у запиті ("not permitted on BrowserConfig from an untrusted request"), тож зовнішній проксі пришпилити через запит не можна.
2. **Захист глибиною** лишається: ядро перевіряє `final_url` і код відповіді (вимога в `01_requirements.md`, 4.2), контейнер без прав на хост.
3. **`/health` не відображає стан браузера.** Потрібен власний healthcheck, що реально використовує браузер. Перевірений варіант: `raw:`-краул із `wait_for: css:p` (експеримент 15). Compose сам не перезапускає `unhealthy`-контейнер, тому скрипт при невдачі завершує PID 1 (сигнал `SIGTERM`), а `restart: unless-stopped` піднімає контейнер заново. Docker-сокет не потрібен.
4. **`restart_browser` не використовувати** (експеримент 12).
5. **Прапорець `success=false` не означає блокування.** У Crawl4AI є безумовна евристика «anti-bot» (`async_webcrawler.py`, без перемикача в конфігурації): крихітні, але цілком реальні відповіді (52 байти, `HTTP 200`) помічаються як `Blocked by anti-bot protection`, хоч HTML отримано й `redirected_status_code` дорівнює 200 (експерименти 7, 9). Адаптер визначає статус за `redirected_status_code`, тілом і `crawl_stats`, а не лише за `success`.
6. **Редірект у заборонену адресу видно як `status_code` 302, `redirected_status_code` 403 і тіло `URL blocked`.** Адаптер відображає це в окремий вид помилки (`ssrf_blocked`), а не в `blocked` (антибот).
7. **`process_iframes: true`** потрібно передавати за замовчуванням, інакше вміст iframe губиться (експерименти 8–9). Параметр у конфігурації.
8. **Навантаження 60 паралельних запитів** витримано без помилок (експеримент 10). Реальну поведінку на повільних зовнішніх сайтах і на понад 60 запитів не міряли.
9. **Монітор Crawl4AI не використовувати як джерело статистики** (експеримент 16 та відсутність атрибуції з `01_crawl4ai.md`).

## Робочі фрагменти для етапу 3

Healthcheck (`hc.py`, монтується в контейнер Crawl4AI):

```python
import json, os, signal, sys, urllib.request

MARKER = "healthcheck-ok-marker"
body = {"urls": ["raw:<html><body><p>" + MARKER + " padding padding padding padding padding</p></body></html>"],
        "crawler_config": {"type": "CrawlerRunConfig", "params": {"cache_mode": "bypass", "wait_for": "css:p"}}}
req = urllib.request.Request(
    "http://127.0.0.1:11235/crawl", data=json.dumps(body).encode(), method="POST",
    headers={"Authorization": "Bearer " + os.environ["CRAWL4AI_API_TOKEN"], "Content-Type": "application/json"})
try:
    result = json.loads(urllib.request.urlopen(req, timeout=15).read())["results"][0]
    healthy = bool(result.get("success")) and MARKER in (result.get("html") or "")
except Exception as exc:
    print("healthcheck failed:", repr(exc), file=sys.stderr)
    healthy = False
if not healthy:
    os.kill(1, signal.SIGTERM)
    sys.exit(1)
```

Це скрипт зі спайку; у проєкті його переписують за вимогами `AGENTS.md` (типи, обробка помилок, лінтери).

Сервіс у compose:

```yaml
crawl4ai:
  image: unclecode/crawl4ai:<pinned-version>   # не :latest
  shm_size: 1g
  environment:
    CRAWL4AI_API_TOKEN: ${CRAWL4AI_API_TOKEN}
    CRAWL4AI_NO_CLOUD_NOTICE: "1"
  networks: [core_net, egress_net]   # core_net: internal; egress_net: звичайна, для виходу в інтернет
  restart: unless-stopped
  volumes: ["./docker/browser/hc.py:/hc/hc.py:ro"]
  healthcheck:
    test: ["CMD", "python", "/hc/hc.py"]
    interval: 10s
    timeout: 20s
    retries: 1
    start_period: 30s
  deploy:
    resources:
      limits:
        memory: 4g
```

Порт Crawl4AI назовні не публікується: ядро звертається до нього по `core_net`.

## Не перевірено

- Реальний сайт зі складним `iframe` і власним скролом (перевірено лише синтетичний `iframe`).
- Вбудовування Telegram-постів.
- Поведінка на повільних зовнішніх сайтах і при понад 60 паралельних запитах.
- Перевірка IPv6-цілей у редіректах (`[::1]`, `fc00::/7`).
- Час першого запиту після холодного старту пулу.
- Зміна поведінки при оновленні версії образа. Образ потрібно закріпити конкретним тегом і прогнати цей стенд знову при оновленні.
