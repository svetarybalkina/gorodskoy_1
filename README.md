# Городской

MVP справочного сервиса официальных ответов городской администрации.

## Рабочая папка

Актуальная рабочая копия проекта после задачи 2 находится в ASCII-пути:

```text
C:\Projects\gorodskoy
```

Старая папка `C:\Users\user-pc\Desktop\Нейро\Городской` оставлена как резерв до ручной приемки переноса.

## Локальный запуск

Основная команда запуска по ТЗ:

```bash
docker compose up
```

После запуска приложение доступно на `http://localhost:8000`.

Проверочные URL:

* `http://localhost:8000/`
* `http://localhost:8000/health`
* `http://localhost:8000/admin`

Если `TELEGRAM_BOT_TOKEN` не задан, веб-приложение продолжает запускаться, а Telegram-бот отключается с понятным сообщением в логах.

## Тесты

Основная команда тестов:

```bash
docker compose run --rm app pytest
```

Дополнительная локальная проверка без Docker возможна через bundled Python Codex:

```powershell
& "C:\Users\user-pc\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m pytest
```

Docker Compose использует явное имя проекта `gorodskoy`, чтобы имя контейнеров и сети было стабильным независимо от имени папки.

16.06.2026 Docker Desktop установлен и приемочная проверка задачи 1 выполнена: `docker compose up -d --build` поднимает приложение, `/`, `/health` и `/admin` отвечают `200`, `docker compose run --rm app pytest` проходит.

## Автозапуск в Windows

Для автозапуска через Планировщик заданий Windows используйте фоновый запуск из актуальной рабочей папки:

```powershell
C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath 'C:\Projects\gorodskoy'; docker compose up -d --build"
```

Команда предполагает, что Docker Desktop уже установлен и запущен. После запуска приложение доступно на `http://localhost:8000`.

Команда остановки:

```powershell
C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath 'C:\Projects\gorodskoy'; docker compose down"
```

## Конфигурация

Пример переменных окружения лежит в `.env.example`. Этот файл попадает в GitHub и должен содержать только заглушки, без реальных логинов, паролей, токенов и секретных ключей. Реальные значения храните только в локальном `.env`.

Для локальной разработки по умолчанию используются:

* `SERVICE_NAME=Городской справочник`
* `PUBLIC_BASE_URL=http://localhost:8000`
* `ADS_ENABLED=false`

Импорт Telegram JSON в админке `/admin/imports` включается только после настройки официального источника в локальном `.env`:

```env
OFFICIAL_TELEGRAM_SOURCE_ID=official_channel_id_or_username
OFFICIAL_TELEGRAM_SOURCE_NAME=Официальный источник администрации
OFFICIAL_TELEGRAM_SOURCE_KIND=official_channel
```

`OFFICIAL_TELEGRAM_SOURCE_ID` должен совпадать с `id`, `username`, `from_id` или другим идентификатором официального источника из стандартной JSON-выгрузки Telegram. Допустимые значения `OFFICIAL_TELEGRAM_SOURCE_KIND`: `official_channel`, `official_bot`, `telegram_bot`, `website`.

Файлы `.env`, базы данных, JSON-выгрузки, экспорты и логи исключены из Git.

Реальные выгрузки Telegram с персональными данными нельзя хранить в репозитории. Для ручной загрузки используйте папку вне проекта, например `C:\Projects\gorodskoy_private\telegram_exports`, или временно кладите файл в `imports/`, который исключен из Git и Docker build context.

## Очистка тестового частичного импорта перед полной загрузкой

Перед полной приемочной загрузкой Telegram JSON можно оставить базу как есть, если дубли из частичного теста не мешают отчету. Если нужна чистая приемочная загрузка без дублей от тестовой выгрузки, сначала выполните dry-run:

```bash
docker compose run --rm app python scripts/cleanup_test_imports.py
```

Команда по умолчанию ничего не удаляет. Она показывает, сколько импортированных черновиков, карточек на проверке и дублей будет затронуто для `OFFICIAL_TELEGRAM_SOURCE_ID` из локального `.env`.

Чтобы применить очистку:

```bash
docker compose run --rm app python scripts/cleanup_test_imports.py --execute
```

Очистка удаляет только материалы, созданные импортом и оставшиеся в статусах `draft`, `needs_review` или `duplicate`, а также их служебные зависимые записи. Опубликованные материалы, ручные карточки, источники, категории, настройки, отчеты импорта и приватные JSON-файлы не удаляются.
