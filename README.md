# 🌵 CheckRP v4.0 — Yuma / Vice City · Telegram + Oracle

Телеграм-бот на Python 3.11+ / aiogram 3, без Web App и без UDP. Данные только из HTTP API:
https://online.g-rp.me/api/v1/projects/arizona-rp

## Что изменилось

- Yuma (ID `25`, обычно 1000 слотов) и **Vice City (ID `58`, 750 слотов)** вместо Show Low.
- Telegram-native сообщения, компактные полоски заполненности, правильные проценты и свободные слоты, обновление сообщения по кнопке.
- Кнопка **⚖️ Сравнение** и команда `/compare`: заполненность и изменения за 10/30/60 минут.
- Тёмные PNG-графики 1 ч / 24 ч / 7 дней. Новый график-сравнение нормирует онлайн к проценту вместимости.
- Подписка: изменения `responding`, низкий онлайн (Yuma порог 980, Vice City 735; выбор из пресетов), пейдей за 10 минут до :00/:30, отчёт в ~21:00 МСК. Нет бесконечных повторов при неизменном API.
- Ежедневный отчёт также отправляется PNG-карточкой с данными; `/report` выводит текст + картинку.
- Миграция существующей SQLite: старые записи Show Low **не переписываются** под Vice City. `/events` → «Архив Show Low». Старые v3 подписки 52 остаются в базе, но больше не отправляются. Подписки на статус Vice City требуют отдельного включения.
- Опциональный ежедневный SQLite backup + systemd timer в `scripts/`.
- `systemd` для круглосуточной работы на Oracle, без открытых публичных HTTP-портов.

### Принципиальные ограничения

- `responding=false` не доказывает рестарт. Источник может запаздывать и может ошибаться.
- API не предоставляет список игроков, их ID и никнеймы; `/player` честно сообщает об ограничении.
- Старые данные Show Low доступны как архив событий, но графики строятся только для текущих двух серверов.
- Пороги выбираются из кнопочных значений: Yuma `950/980/990`, Vice City `700/735/740`.
- История Vice City накопится **после** запуска v4; исторические точки никогда не подделываются.
- Нет гарантий 100% доступности стороннего API или самого Oracle Free Tier.

## Развёртывание обновления на существующей Oracle VPS

> Важно: **у тебя есть рабочая версия v3!** Сначала сделай резервную копию и не запускай две копии с одним Telegram-токеном.

### Вариант А: сначала обновить GitHub

Загрузи содержимое этого ZIP в корень репозитория `uppercotangle-alt/arizona-monitor` (через GitHub Desktop или локальный `git`). Не загружай `.env`, `.sqlite3`, приватные SSH-ключи и папку `data/`.

На сервере (в PowerShell уже после SSH-подключения):

```bash
cd /home/ubuntu/checkrp
sudo systemctl stop checkrp
# Горячая SQLite-копия, безопасная при WAL; бот уже остановлен.
mkdir -p backups
python3 - <<'PY'
from pathlib import Path
import sqlite3, shutil
src=Path('data/checkrp.sqlite3')
if src.exists():
    dst=Path('backups/pre-v4.sqlite3')
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as t:
        s.backup(t)
    print('Backup:',dst)
PY
# Сохраним старый код для отката
umask 077 && tar -czf ~/checkrp-pre-v4.tgz --exclude=.venv -C ~ checkrp
git pull --ff-only
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
sudo systemctl start checkrp
sudo systemctl status checkrp --no-pager -l
sudo journalctl -u checkrp -n 40 --no-pager -l
```

Для безопасного отката лучше сохранить git commit SHA (`git rev-parse HEAD`) до обновления. Архив `~/checkrp-pre-v4.tgz` содержит старый код и базу, но не виртуальное окружение.

### Вариант Б: загрузить ZIP напрямую (без GitHub)

На **Windows PowerShell** (не в Ubuntu), если ZIP сохранён в Downloads:

```powershell
& "$env:WINDIR\System32\OpenSSH\scp.exe" -i "$env:USERPROFILE\.ssh\checkrp_oracle" "$env:USERPROFILE\Downloads\CheckRP-v4-Yuma-ViceCity.zip" ubuntu@152.70.183.27:~/checkrp-v4.zip
```

На сервере:

```bash
sudo systemctl stop checkrp
cd ~/checkrp
mkdir -p backups
python3 - <<'PY'
from pathlib import Path
import sqlite3
src=Path('data/checkrp.sqlite3')
if src.exists():
    with sqlite3.connect(src) as s, sqlite3.connect('backups/pre-v4.sqlite3') as t:
        s.backup(t)
PY
sudo apt-get install -y unzip
unzip -o ~/checkrp-v4.zip -d ~/checkrp
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
sudo systemctl start checkrp
sudo journalctl -u checkrp -n 40 --no-pager -l
```

Zip не содержит `.env` и не трогает `data/checkrp.sqlite3`.

**Если ошибка при обновлении:** не оставляй старую службу запущенной на несовместимом коде. Сохрани вывод `journalctl`, останови её и верни код из резервной копии или прежнего коммита. База обратно совместима с v3 по сохранённым колонкам `status_52`/`slots_52`, но резервная копия всё равно необходима.

### Проверка после запуска

1. `systemctl is-enabled checkrp` и `systemctl is-active checkrp` → `enabled`, `active`.
2. Telegram: `/check`, Vice City, `/compare`, `/stats` → Yuma/Vice City, `/events` → Архив Show Low, `/report`.
3. `/notify`: новые настройки Vice City **отдельно включи**. Порог Vice City выбирается в кнопке «Настроить пороги».
4. `/health` от администратора; затем `sudo journalctl -u checkrp -n 60 --no-pager -l`.
5. Секреты в `.env` не показывай и не отправляй скриншотами; ранее раскрытый токен перевыпусти через BotFather.

### Автоматические SQLite-бэкапы (необязательно)

```bash
sudo cp scripts/checkrp-backup.service scripts/checkrp-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now checkrp-backup.timer
systemctl list-timers | grep checkrp
```

По умолчанию сохраняет семь последних файлов в `~/checkrp/backups`. Эти бэкапы в той же VPS — для серьёзной защиты от потери машины делай периодически *внешнюю* резервную копию.

## Конфигурация

В `.env` на VPS (не включён в ZIP):

```env
BOT_TOKEN=PASTE_NEW_TOKEN_FROM_BOTFATHER
ADMIN_ID=YOUR_TELEGRAM_NUMERIC_ID
POLL_SECONDS=20
DB_PATH=/home/ubuntu/checkrp/data/checkrp.sqlite3
```

`systemd` service файл сохраняется существующий `/etc/systemd/system/checkrp.service`; в этом обновлении его не нужно пересоздавать.

## Тесты

```bash
python -m unittest discover -s tests -v
```

Юнит-тесты не требуют токен. Они проверяют парсинг, API timestamps, дедуп, подтверждение недоступности, миграцию v3, пороги и архив. Для теста Telegram handlers требуется реальный aiogram в среде VPS; live Telegram/API интеграция проверяется отдельно после деплоя.
