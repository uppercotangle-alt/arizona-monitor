#!/usr/bin/env python3
"""CheckRP v3 — Telegram monitoring of Arizona RP over HTTP only.

Requires aiogram 3, aiohttp, matplotlib. For Python 3.11+.
Keep BOT_TOKEN in the hosting environment, never in GitHub.
"""
from __future__ import annotations
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import aiohttp
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, CallbackQuery, Message, BufferedInputFile

from checkrp.api import APIClient, APIError
from checkrp.charts import PERIODS, render_png
from checkrp.core import SLOT_LIMIT, TZ, UTC, SERVERS, Sample, Snapshot, payday_reminder, daily_window, dt_str
from checkrp.db import DB, Event, Preference
from checkrp.ui import (HELP, PLAYER_NOTICE, dashboard, event_text, events_text, kb_back,
                        kb_charts, kb_home, kb_settings, server_details, settings_text, stats_text,
                        when)

log = logging.getLogger("checkrp")
router = Router()


class ClickLimiter:
    def __init__(self) -> None:
        self.last: dict[int, float] = {}

    def allow(self, uid: int, seconds: float = 2.0) -> bool:
        now = time.monotonic()
        if now - self.last.get(uid, -1e9) < seconds:
            return False
        self.last[uid] = now
        if len(self.last) > 5000:
            self.last = {k: v for k, v in self.last.items() if now - v < 300}
        return True


@dataclass
class App:
    db: DB
    api: APIClient
    poll_seconds: int
    admin_id: int
    limiter: ClickLimiter = field(default_factory=ClickLimiter)


async def latest(app: App) -> tuple[Snapshot | None, str | None]:
    try:
        snap = await app.api.fetch()
        return snap, app.api.last_error
    except APIError:
        return app.api.latest, app.api.last_error or "Ошибка API"


def user_pref(app: App, chat_id: int, private: bool) -> Preference | None:
    return app.db.preference(chat_id) if private else None


async def show_home(message: Message, app: App) -> None:
    snap, err = await latest(app)
    pref = user_pref(app, message.chat.id, message.chat.type == "private")
    await message.answer(dashboard(app.db, snap, err), reply_markup=kb_home(pref),
                         disable_web_page_preview=True)


async def edit(call: CallbackQuery, text: str, markup) -> None:
    if not isinstance(call.message, Message):
        return
    try:
        await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        log.warning("Cannot edit message: %s", type(exc).__name__)
        try:
            await call.message.answer(text, reply_markup=markup, disable_web_page_preview=True)
        except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError):
            log.warning("Unable to send new message after edit failure")


@router.message(CommandStart())
@router.message(Command("check"))
async def check(message: Message, app: App) -> None:
    if message.from_user and not app.limiter.allow(message.from_user.id):
        await message.answer("⏳ Подожди пару секунд перед повторной проверкой.")
        return
    await show_home(message, app)


@router.message(Command("subscribe"))
async def subscribe(message: Message, app: App) -> None:
    if message.chat.type != "private":
        await message.answer("🔐 Подписки можно включить только в личном чате.")
        return
    pref = app.db.set_enabled(message.chat.id, True)
    await message.answer("🔔 <b>Подписка включена.</b> Настрой события ниже.",
                         reply_markup=kb_settings(pref))


@router.message(Command("unsubscribe"))
async def unsubscribe(message: Message, app: App) -> None:
    if message.chat.type != "private":
        await message.answer("🔐 Управляй подписками в личном чате.")
        return
    pref = app.db.set_enabled(message.chat.id, False)
    await message.answer("🔕 Подписка отключена.", reply_markup=kb_settings(pref))


@router.message(Command("notify"))
async def notify(message: Message, app: App) -> None:
    if message.chat.type != "private":
        await message.answer("🔐 Настройки только в личном чате.")
        return
    pref = app.db.preference(message.chat.id)
    await message.answer(settings_text(pref), reply_markup=kb_settings(pref))


@router.message(Command("events"))
async def events(message: Message, app: App) -> None:
    await message.answer(events_text(app.db.recent_events()), reply_markup=kb_back("events"))


@router.message(Command("stats"))
async def stats(message: Message) -> None:
    await message.answer("📈 <b>ГРАФИКИ ОНЛАЙНА</b>\nВыбери сервер и период.\n\n"
                         "История начинает накапливаться с момента запуска v3.",
                         reply_markup=kb_charts())


@router.message(Command("report"))
async def report(message: Message, app: App) -> None:
    now = datetime.now(UTC)
    start = now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    await message.answer(stats_text(app.db, start, now, "СЕГОДНЯ"), reply_markup=kb_back("report"))


@router.message(Command("player"))
async def player(message: Message) -> None:
    await message.answer(PLAYER_NOTICE, reply_markup=kb_back())


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(HELP, reply_markup=kb_back(), disable_web_page_preview=True)


@router.message(Command("health"))
async def health(message: Message, app: App) -> None:
    if not app.admin_id or not message.from_user or app.admin_id != message.from_user.id:
        await message.answer("⛔ Недоступно.")
        return
    count = len(app.db.subscribers())
    snapshot = app.api.latest
    api_time = when(snapshot.fetched_at) if snapshot else "ещё не обращались"
    from html import escape
    err = escape(app.api.last_error or "нет")
    await message.answer(f"🛠 <b>HEALTH</b>\nПодписчиков: {count}\n"
                         f"Последний ответ: {api_time}\n"
                         f"Ошибка API: {err[:180]}\nОпрос: {app.poll_seconds} сек.\nВремя: МСК")


@router.callback_query()
async def callback(call: CallbackQuery, app: App) -> None:
    data = call.data or ""
    message = call.message
    private = isinstance(message, Message) and message.chat.type == "private"
    chat_id = message.chat.id if isinstance(message, Message) else call.from_user.id
    if data.startswith("toggle:") or data == "settings":
        if not private:
            await call.answer("Настройки только в личном чате", show_alert=True)
            return
        if data.startswith("toggle:"):
            name = data.split(":", 1)[1]
            try:
                if name == "enabled":
                    current = app.db.preference(chat_id)
                    pref = app.db.set_enabled(chat_id, not current.enabled)
                else:
                    pref = app.db.toggle(chat_id, name)
            except ValueError:
                await call.answer("Неизвестная настройка")
                return
        else:
            pref = app.db.preference(chat_id)
        await call.answer("Сохранено" if data.startswith("toggle:") else "")
        await edit(call, settings_text(pref), kb_settings(pref))
        return
    if data == "home" or data.startswith("g:"):
        if not app.limiter.allow(call.from_user.id, 0.8):
            await call.answer("⏳ Слишком быстро. Подожди секунду.")
            return
    if data == "home":
        await call.answer("🔄 Обновляю")
        snap, err = await latest(app)
        await edit(call, dashboard(app.db, snap, err), kb_home(user_pref(app, chat_id, private)))
    elif data in ("s:25", "s:52"):
        await call.answer("🔎 Проверяю")
        sid = int(data.split(":")[1])
        snap, err = await latest(app)
        await edit(call, server_details(app.db, snap, sid, err), kb_back(data))
    elif data == "events":
        await call.answer()
        await edit(call, events_text(app.db.recent_events()), kb_back("events"))
    elif data == "help":
        await call.answer()
        await edit(call, HELP, kb_back())
    elif data == "report":
        await call.answer()
        now = datetime.now(UTC)
        start = now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        await edit(call, stats_text(app.db, start, now, "СЕГОДНЯ"), kb_back("report"))
    elif data == "charts":
        await call.answer()
        await edit(call, "📈 <b>ИСТОРИЯ ОНЛАЙНА</b>\nВыбери сервер и период.", kb_charts())
    elif data.startswith("g:"):
        parts = data.split(":")
        if len(parts) != 3 or parts[1] not in ("25", "52") or parts[2] not in PERIODS:
            await call.answer("Неизвестный период")
            return
        sid, period = int(parts[1]), parts[2]
        points = app.db.points(sid, datetime.now(UTC) - PERIODS[period])
        if len(points) < 2:
            await call.answer("Мало истории. Дай боту накопить хотя бы две минуты данных.", show_alert=True)
            return
        await call.answer("📊 Рисую график")
        try:
            png = await asyncio.to_thread(render_png, points, SERVERS[sid], period)
            if png is None:
                return
            now = datetime.now(UTC)
            s = app.db.stats(sid, now - PERIODS[period], now)
            avg = f"{float(s['avg_online']):.0f}" if s["avg_online"] is not None else "—"
            caption = (f"📈 <b>{SERVERS[sid]} · {period}</b>\n"
                       f"Средний онлайн: {avg}\nПик: {s['peak'] or '—'}\n"
                       f"Измерений: {s['n']}\n<i>По данным HTTP API · МСК</i>")
            if isinstance(message, Message):
                await message.answer_photo(BufferedInputFile(png, filename=f"{sid}_{period}.png"), caption=caption)
        except Exception:
            log.exception("Unable to render graph")
            if isinstance(message, Message):
                await message.answer("⚠️ Не удалось построить график. Попробуй позже.")
    else:
        await call.answer("Кнопка устарела. Нажми /check.")


async def send(bot: Bot, app: App, chat_id: int, text: str) -> bool:
    try:
        await bot.send_message(chat_id, text, disable_web_page_preview=True)
        return True
    except TelegramForbiddenError:
        app.db.disable(chat_id)
        return False
    except TelegramRetryAfter as exc:
        await asyncio.sleep(min(float(exc.retry_after), 30))
        try:
            await bot.send_message(chat_id, text, disable_web_page_preview=True)
            return True
        except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter):
            log.warning("Telegram send failed after retry for %s", chat_id)
            return False
    except (TelegramBadRequest, TelegramNetworkError):
        log.warning("Telegram send failed for %s", chat_id)
        return False


async def distribute(bot: Bot, app: App, chats: list[int], text: str,
                     unique: tuple[str, str] | None = None) -> None:
    for chat_id in chats:
        if unique is not None and app.db.was_sent(chat_id, *unique):
            continue
        if await send(bot, app, chat_id, text) and unique is not None:
            app.db.mark_sent(chat_id, *unique)
        await asyncio.sleep(0.055)


async def monitor_loop(bot: Bot, app: App) -> None:
    log.info("Monitoring %s with HTTP API every %s sec", list(SERVERS), app.poll_seconds)
    last_prune_day = ""
    while True:
        try:
            snap = await app.api.fetch()
            events, samples = app.db.collect(snap)
            if snap.source_problem:
                log.warning("Possible mass upstream outage; skipping automatic alerts")
            for e in events:
                subscribers = [p.chat_id for p in app.db.subscribers() if p.allow_status(e.server_id)]
                await distribute(bot, app, subscribers, event_text(e),
                                 unique=(f"event:{e.server_id}:{e.kind}", dt_str(e.at)))
            for sample in samples:
                # Samples are unique by API source timestamp: no repeated alert for cached response.
                if sample.responding:
                    slots = app.db.slot_alert_chats(sample)
                    if slots:
                        free = max(0, sample.max_slots - sample.online)
                        text = (f"👥 <b>{SERVERS[sample.server_id]} · есть свободные слоты!</b>\n"
                                f"Онлайн опустился ниже {SLOT_LIMIT}: "
                                f"<b>{sample.online} / {sample.max_slots}</b>\n"
                                f"Свободно: {free}\n🕒 {when(sample.source_at)}")
                        await distribute(bot, app, slots, text,
                                         unique=(f"slots:{sample.server_id}", dt_str(sample.source_at)))
            day = datetime.now(UTC).date().isoformat()
            if day != last_prune_day:
                app.db.prune(datetime.now(UTC))
                last_prune_day = day
        except APIError:
            pass  # Do not transform an HTTP error into a game-server restart.
        except Exception:
            log.exception("Monitor iteration failed")
        await asyncio.sleep(app.poll_seconds)


async def scheduled_loop(bot: Bot, app: App) -> None:
    """Moscow clock notifications, independent of HTTP API health."""
    while True:
        try:
            now = datetime.now(UTC)
            due = payday_reminder(now)
            if due:
                bucket, payday = due
                pay_chats = [p.chat_id for p in app.db.subscribers() if p.payday]
                if pay_chats:
                    txt = ("💰 <b>СКОРО ПЕЙДЕЙ!</b>\n\n"
                           "⏳ До пейдея примерно <b>10 минут</b>.\n"
                           f"🕒 Время пейдея: <b>{payday.strftime('%H:%M')} МСК</b>\n"
                           "🌵 Arizona RP · напоминание по расписанию")
                    await distribute(bot, app, pay_chats, txt, unique=("payday", bucket))
            today_window = daily_window(now)
            if today_window:
                start, end = today_window
                report_chats = [p.chat_id for p in app.db.subscribers() if p.daily]
                if report_chats:
                    summary = stats_text(app.db, start, end, "ЗА СЕГОДНЯ · 21:00 МСК")
                    bucket = end.astimezone(TZ).date().isoformat()
                    await distribute(bot, app, report_chats, summary, unique=("daily", bucket))
        except Exception:
            log.exception("Scheduler iteration failed")
        await asyncio.sleep(10)


async def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("BOT_TOKEN is missing: set it in your hosting Environment Variables")
    poll = int(os.getenv("POLL_SECONDS", "20"))
    if not 10 <= poll <= 300:
        raise ValueError("POLL_SECONDS must be between 10 and 300")
    admin_id = int(os.getenv("ADMIN_ID", "0"))
    db_file = os.getenv("DB_PATH", "./data/checkrp.sqlite3")
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    timeout = aiohttp.ClientTimeout(total=12, connect=5)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "CheckRP/3.0"}) as session:
        db = DB(db_file)
        api = APIClient(session)
        app = App(db=db, api=api, poll_seconds=poll, admin_id=admin_id)
        bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dp = Dispatcher()
        dp.include_router(router)
        dp["app"] = app
        tasks: list[asyncio.Task] = []
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            await bot.set_my_commands([
                BotCommand(command="check", description="🌵 Онлайн Yuma и Show Low"),
                BotCommand(command="notify", description="🔔 Настройки уведомлений"),
                BotCommand(command="stats", description="📈 Графики онлайна"),
                BotCommand(command="events", description="📋 История отключений"),
                BotCommand(command="report", description="📅 Сегодняшняя статистика"),
                BotCommand(command="player", description="🔎 О поиске игрока"),
                BotCommand(command="help", description="ℹ️ О боте"),
            ])
            tasks = [asyncio.create_task(monitor_loop(bot, app), name="monitor"),
                     asyncio.create_task(scheduled_loop(bot, app), name="scheduler")]
            await dp.start_polling(bot, close_bot_session=False,
                                   allowed_updates=dp.resolve_used_update_types())
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await bot.session.close()
            db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    except ValueError as exc:
        # Never print the token as part of an error message.
        print(f"Config error: {exc}", file=sys.stderr)
        raise SystemExit(1)
