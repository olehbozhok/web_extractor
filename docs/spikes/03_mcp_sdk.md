# Спайк 03: MCP Python SDK 2.x

Дата: 2026-11-09. Мета: перевірити пункти «не перевірено» з `docs/03_mcp_interface.md`, розділ 10, перед реалізацією M5. SDK: `mcp` 2.3.0 (увага: у 2.x `FastMCP` перейменовано на `MCPServer`, поля типів у snake_case — `input_schema`, `is_error`, `structured_content`; клієнтський генератор називається `streamable_http_client` і повертає два потоки, без `get_session_id`).

## Метод

Скрипт у тимчасовій директорії піднімав `MCPServer.streamable_http_app(stateless_http=True, json_response=True)` на uvicorn (loopback, випадковий порт) і підключався справжнім MCP-клієнтом у процесі.

## Результати

| # | Питання | Результат |
|---|---|---|
| 1 | Безстановий Streamable HTTP | Працює: `stateless_http=True`; клієнт без сесії виконує `initialize`, `tools/list`, `call_tool` |
| 2 | Схеми з анотацій | `input_schema` генерується з типової сигнатури (обов'язкові/опційні, дефолти); `output_schema` генерується лише якщо функція повертає модель/dict |
| 3 | `structuredContent` | Передається клієнтові коректно і без `outputSchema` (специфікація робить його обов'язковим лише разом зі схемою, але клієнт читає і так) |
| 4 | Дублювання тіла в тексті | **Так**: при звичайному поверненні dict SDK серіалізує JSON у текстовий блок сам, тож тіло опинилося б у контексті двічі. Рішення: функція інструмента повертає готовий `CallToolResult` — тоді текст і `structured_content` повністю під нашим контролем (перевірено: сторонній JSON не додається) |
| 5 | `outputSchema` при `CallToolResult` | Дорівнює None навіть із `structured_output=True` (SDK ніде взяти схему). Прийнято: не рекламуємо `outputSchema`; `structured_content` генерується з типізованих моделей Pydantic, клієнти (перевірено стандартним `ClientSession`) його отримують. Відхилення від бажання «outputSchema завжди» зафіксовано тут і в `docs/03`, розділ 10 |
| 6 | Анотації | `ToolAnnotations(read_only_hint, destructive_hint, idempotent_hint, open_world_hint)` потрапляють у `tools/list` як є |
| 7 | Порядок `tools/list` | Детермінований між викликами |
| 8 | Валідація аргументів | Невалідні аргументи → `is_error=True`, текст «Error executing tool …: N validation errors …» (SDK валідує за схемою сам) |
| 9 | Невідомий інструмент | `is_error=True`, «Unknown tool: …» |
| 10 | Заголовки HTTP у функції інструмента | Параметр, анотований `Context` (з `mcp.server.mcpserver`), ін'єктується; `context.headers` містить HTTP-заголовки запиту, включно з `x-agent-id`, заданим клієнтом через `create_mcp_http_client(headers=...)` |
| 11 | `context.session_id` | Наявний у `Context`; у безстановому режимі очікуємо None/порожній — поле події просто пишеться як є |

## Наслідки для реалізації

- Інструменти оголошуються через `@server.tool(...)` з анотаціями; аргументи — типізовані моделі Pydantic (схема генерується автоматично), а тіло відповіді будує наш шар форматування, повертаючи `CallToolResult`.
- `structured_content` будується з тієї ж моделі метаданих (без тіла) — вимога 4.1 виконана, хоча сам `outputSchema` не рекламується (пункт 5).
- `agent` береться з аргументу або `context.headers["x-agent-id"]`, інакше `unknown` (docs/03, розділ 6).

## Що лишилось неперевіреним

- Як конкретний клієнт (Claude Code) подає `structuredContent` моделі (позакодовий тест, M10).
- Реальні токенові витрати на кирилиці (M10).
