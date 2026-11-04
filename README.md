# 🌵 Arizona RP Monitor (Yuma + Show Low)

Telegram-бот на **Python + aiogram 3**, работающий только через сторонний HTTP API: `https://online.g-rp.me/api/v1/projects/arizona-rp`. **UDP / SA-MP Query не используется.**

## Возможности

- `/check` — красивая карточка Yuma и Show Low: онлайн, заполненность, статус, возраст данных.
- Кнопки «Yuma», «Show Low», «Обновить», «Уведомления», «События», «О боте».
- `/subscribe` и `/unsubscribe` — включение/выключение уведомлений (в личном чате).
- `/events` — последние события в SQLite.
- `/health` — диагностика, только для администратора (если задан `ADMIN_ID`).
- Фоновый опрос API раз в 20 секунд (настраивается).
- Уведомления о **потере / восстановлении ответа**, при двух последовательных **разных обновлениях `updated_at`**.
- Устаревшие данные, ошибки API, подозрительные массовые сбои источника и повторные одинаковые ответы не запускают ложных оповещений.
- Ограничение частых нажатий и общий минимальный интервал обращения к API.

**Обратите внимание:** g-rp.me — сторонний мониторинг и может показывать задержанные значения. Параметр `responding=false` не доказывает, что разработчики Arizona RP запустили рестарт: сервер мог быть недоступен по другим причинам. Бот **не заявляет**, что достоверно установил причину отключения, и не может оповестить быстрее, чем обновляется сам API.

## Структура

```text
bot.py
monitor/
  api.py       # HTTP-запросы, JSON и свежесть данных
  config.py    # переменные окружения
  logic.py     # подтверждение переходов статуса
  storage.py   # SQLite подписчики и события
  ui.py        # кнопки и HTML Telegram
  __init__.py
requirements.txt
Dockerfile
.env.example
.gitignore
.dockerignore
tests/test_monitor.py
```

## GitHub и EthioDeploy

1. Создайте новый репозиторий и загрузите **все файлы из проекта** (папку `monitor` тоже). Файлы `.env` и базы данных в GitHub **не загружайте**.
2. В EthioDeploy подключите репозиторий. `Monorepo Directory` оставьте пустым, если `bot.py` лежит в корне.
3. **Самый надёжный вариант:** `Builder = Auto (Dockerfile if present)` — в проекте уже есть `Dockerfile`. Поля `Install Command`, `Build Command`, `Start Command` при этом не нужны.
4. Если хотите `Auto-detect stack (ignore Dockerfile)`:
   - Install Command: `pip install -r requirements.txt`
   - Build Command: пусто
   - Start Command: `python bot.py`
5. В Environment Variables добавьте каждую переменную **отдельной строкой**:

   | Key | Value |
   | --- | --- |
   | `BOT_TOKEN` | **Новый токен, полученный в BotFather** |
   | `ADMIN_ID` | Telegram user ID администратора (необязательно) |
   | `POLL_SECONDS` | `20` |
   | `MAX_DATA_AGE_SECONDS` | `180` |
   | `TZ` | `Europe/Kyiv` |
   | `DB_PATH` | `./data/monitor.sqlite3` |

   Для токена включите `Sensitive` и `Runtime only`.
6. Запустите Deploy. Откройте бота, используйте `/start`, затем кнопку «Включить уведомления».

### Важное о сохранении подписок

SQLite хранится в файле `DB_PATH`. Если EthioDeploy **сбрасывает файловую систему контейнера после redeploy**, подписки и история пропадут. Для долговременной работы подключите постоянный диск/volume к `/app/data` (если ваш тариф/хостинг это позволяет), либо позже перенесите хранение в PostgreSQL. Не запускайте две копии polling-бота с одним токеном одновременно — Telegram будет выдавать конфликт `getUpdates`.

## Локальный запуск

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env   # Windows: copy .env.example .env
# Вставьте токен в .env, не коммитьте его
python bot.py
```

Для тестов:

```bash
pip install pytest
python -m pytest -q
```

## Диагностика

- `/ping` отвечает, а `/check` не показывает свежий онлайн: проверьте `updated_at` и состояние `https://online.g-rp.me/api/v1/projects/arizona-rp`. Бот честно укажет устаревшие данные.
- API периодически недоступен: это не считается рестартом сервера. Следующая проверка выполняется автоматически.
- Не приходят уведомления: нужно включить их кнопкой или командой `/subscribe`, а также проверить сохранение базы между деплоями.
- При массовом `responding=false` бот приостанавливает оповещения во избежание ложных рестартов.
- `BOT_TOKEN` никогда не добавляйте в исходники. Если токен ранее публиковался, отзовите его через BotFather (`/revoke`) и выпустите новый.

## Что можно добавить дальше

Историю онлайна и графики, отдельные уведомления по серверам, статистику персонажей **только после обнаружения подходящего API**. В указанном источнике нет данных о персонажах по нику или ID.
