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

Пример переменных окружения лежит в `.env.example`. Для локальной разработки по умолчанию используются:

* `SERVICE_NAME=Городской справочник`
* `PUBLIC_BASE_URL=http://localhost:8000`
* `ADS_ENABLED=false`

Файлы `.env`, базы данных, JSON-выгрузки, экспорты и логи исключены из Git.
