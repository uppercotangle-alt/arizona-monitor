#!/usr/bin/env python3
"""CheckRP v4.5 — Yuma / Vice City Telegram native monitor, HTTP API only.

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
from checkrp.charts import PERIODS, render_png, render_comparison_png, render_daily_report_png
from checkrp.core import SERVER_LIMITS, TZ, UTC, SERVERS, Sample, Snapshot, payday_reminder, daily_window, dt_str
from checkrp.db import DB, Event, Preference
from checkrp.ui import (HELP, PLAYER_NOTICE, dashboard, event_text, events_text, kb_back,
                        kb_charts, kb_home, kb_settings, kb_compare, kb_events, kb_thresholds,
                        kb_server_settings, kb_global_settings, kb_server_details, kb_analytics,
                        kb_chart_photo, kb_report, analytics_text, server_settings_text,
                        schedule_settings_text, thresholds_text, comparison_text,
                        server_details, settings_text, stats_text, when, PERIOD_TIMES)

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
    await message.answer(events_text(app.db.recent_events()), reply_markup=kb_events())


@router.message(Command("stats"))
async def stats(message: Message, app: App) -> None:
    await message.answer(analytics_text(app.db, app.api.latest, app.api.last_error),
                         reply_markup=kb_analytics())


def report_interval(period: str, now: datetime) -> tuple[datetime, datetime, str]:
    if period == "today":
        start = now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        return start, now, "СЕГОДНЯ · МСК"
    if period == "24h":
        return now - timedelta(hours=24), now, "24 ЧАСА · МСК"
    if period == "7d":
        return now - timedelta(days=7), now, "7 ДНЕЙ · МСК"
    raise ValueError("unknown report period")


@router.message(Command("report"))
async def report(message: Message, app: App) -> None:
    now = datetime.now(UTC)
    start, end, title = report_interval("today", now)
    await message.answer(stats_text(app.db, start, end, title), reply_markup=kb_report("today"))


@router.message(Command("compare"))
async def compare(message: Message, app: App) -> None:
    snap, err = await latest(app)
    await message.answer(comparison_text(app.db, snap, err, 30), reply_markup=kb_compare())


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

    # All subscriptions are private-chat preferences; never mutate in groups.
    sensitive = (data == "settings" or data == "thresholds" or
                 data.startswith(("toggle:", "limit:", "limits:", "nt:")))
    if sensitive:
        if not private:
            await call.answer("Настройки доступны только в личном чате", show_alert=True)
            return
        try:
            if data.startswith("toggle:"):
                name = data.partition(":")[2]
                if name == "enabled":
                    current = app.db.preference(chat_id)
                    pref = app.db.set_enabled(chat_id, not current.enabled)
                else:
                    pref = app.db.toggle(chat_id, name)
            elif data.startswith("limit:"):
                _, sid_s, n_s = data.split(":")
                pref = app.db.set_threshold(chat_id, int(sid_s), int(n_s))
            else:
                pref = app.db.preference(chat_id)
        except (ValueError, TypeError):
            await call.answer("Недопустимый параметр", show_alert=True)
            return

        if data.startswith("limit:"):
            sid = int(data.split(":")[1])
            text, markup = thresholds_text(pref, sid), kb_thresholds(pref, sid)
        elif data.startswith("limits:"):
            sid = int(data.partition(":")[2]) if data.partition(":")[2] in ("25", "58") else -1
            if sid not in SERVERS:
                await call.answer("Сервер неизвестен")
                return
            text, markup = thresholds_text(pref, sid), kb_thresholds(pref, sid)
        elif data == "thresholds":
            text, markup = thresholds_text(pref), kb_thresholds(pref)
        elif data.startswith("nt:"):
            part = data.partition(":")[2]
            if part == "global":
                text, markup = schedule_settings_text(pref), kb_global_settings(pref)
            elif part in ("25", "58"):
                sid = int(part)
                text, markup = server_settings_text(pref, sid), kb_server_settings(pref, sid)
            else:
                await call.answer("Неизвестная категория")
                return
        elif data.startswith("toggle:status_") or data.startswith("toggle:slots_"):
            sid_s = data.rsplit("_", 1)[1]
            sid = int(sid_s)
            text, markup = server_settings_text(pref, sid), kb_server_settings(pref, sid)
        elif data in ("toggle:payday", "toggle:daily"):
            text, markup = schedule_settings_text(pref), kb_global_settings(pref)
        else:
            text, markup = settings_text(pref), kb_settings(pref)
        await call.answer("✅ Сохранено" if data.startswith(("toggle:", "limit:")) else "")
        await edit(call, text, markup)
        return

    if data in ("home", "charts") or data.startswith(("g:", "rp:")):
        if not app.limiter.allow(call.from_user.id, 0.85):
            await call.answer("⏳ Чуть медленнее")
            return

    if data == "home":
        await call.answer("↻ Обновляю")
        snap, err = await latest(app)
        await edit(call, dashboard(app.db, snap, err), kb_home(user_pref(app, chat_id, private)))
    elif data.startswith("s:") and data[2:].isdigit() and int(data[2:]) in SERVERS:
        sid = int(data[2:])
        await call.answer("🔎 Проверяю")
        snap, err = await latest(app)
        await edit(call, server_details(app.db, snap, sid, err), kb_server_details(sid))
    elif data in ("charts",) or data.startswith("a:"):
        if data == "charts":
            selected, period = "both", "24h"
        else:
            parts = data.split(":")
            if len(parts) != 3 or parts[1] not in ("25", "58", "both") or parts[2] not in PERIODS:
                await call.answer("Неизвестный период", show_alert=True)
                return
            selected, period = parts[1], parts[2]
        await call.answer()
        snap, err = await latest(app)
        await edit(call, analytics_text(app.db, snap, err, selected, period), kb_analytics(selected, period))
    elif data == "events":
        await call.answer()
        await edit(call, events_text(app.db.recent_events()), kb_events())
    elif data == "archive":
        await call.answer()
        await edit(call, events_text(app.db.recent_events(archive=True), archive=True), kb_events(archive=True))
    elif data.startswith("compare:"):
        try:
            minutes = int(data.partition(":")[2])
        except ValueError:
            minutes = -1
        if minutes not in (10, 30, 60):
            await call.answer("Неизвестный период")
            return
        await call.answer()
        snap, err = await latest(app)
        await edit(call, comparison_text(app.db, snap, err, minutes), kb_compare(minutes))
    elif data == "help":
        await call.answer()
        await edit(call, HELP, kb_back())
    elif data == "report" or data.startswith("r:"):
        period = "today" if data == "report" else data.partition(":")[2]
        try:
            start, end, title = report_interval(period, datetime.now(UTC))
        except ValueError:
            await call.answer("Неизвестный период")
            return
        await call.answer()
        await edit(call, stats_text(app.db, start, end, title), kb_report(period))
    elif data.startswith("rp:"):
        period = data.partition(":")[2]
        try:
            start, end, title = report_interval(period, datetime.now(UTC))
        except ValueError:
            await call.answer("Неизвестный отчёт")
            return
        await call.answer("🖼 Формирую отчёт")
        if not isinstance(message, Message):
            return
        try:
            figures = {sid: app.db.stats(sid, start, end) for sid in SERVERS}
            png = await asyncio.to_thread(render_daily_report_png, figures, title)
            await message.answer_photo(BufferedInputFile(png, filename=f"report_{period}.png"),
                                       caption=f"📅 <b>CheckRP · {title}</b>\nПо истории HTTP API · МСК",
                                       reply_markup=kb_report(period))
        except Exception:
            log.exception("Could not render report image")
            await message.answer("⚠️ Не удалось сформировать PNG-отчёт.")
    elif data.startswith("g:"):
        parts = data.split(":")
        if len(parts) != 3 or parts[1] not in ({str(x) for x in SERVERS} | {"both"}) or parts[2] not in PERIODS:
            await call.answer("Неизвестный график")
            return
        selected, period = parts[1], parts[2]
        await call.answer("📈 Строю график")
        if not isinstance(message, Message):
            return
        since = datetime.now(UTC) - PERIODS[period]
        try:
            if selected == "both":
                series = {sid: app.db.points(sid, since) for sid in SERVERS}
                limits = {sid: (app.api.latest.servers[sid].max_slots if app.api.latest else (1000 if sid == 25 else 750))
                          for sid in SERVERS}
                png = await asyncio.to_thread(render_comparison_png, series, period, limits)
                caption = f"⚖️ <b>Yuma × Vice City</b> · {period}\nЗаполненность в % от вместимости каждого сервера."
            else:
                sid = int(selected)
                points = app.db.points(sid, since)
                capacity = app.api.latest.servers[sid].max_slots if app.api.latest else (1000 if sid == 25 else 750)
                png = await asyncio.to_thread(render_png, points, SERVERS[sid], period, capacity)
                caption = f"📈 <b>{SERVERS[sid]}</b> · {period}\nИстория онлайна · МСК · HTTP API"
            if png is None:
                await message.answer("⌛ Пока недостаточно истории для выбранного графика.",
                                     reply_markup=kb_analytics(selected, period))
                return
            await message.answer_photo(BufferedInputFile(png, filename=f"checkrp_{selected}_{period}.png"),
                                       caption=caption, reply_markup=kb_chart_photo(selected, period))
        except Exception:
            log.exception("Unable to render graph")
            await message.answer("⚠️ Не удалось построить график. Попробуй позже.")
    else:
        await call.answer("Эта кнопка устарела. Открой /check.")


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
                # The source timestamp must be new; no duplicate alerts on cached API data.
                if sample.responding:
                    for chat_id, threshold in app.db.slot_alert_chats(sample):
                        free = max(0, sample.max_slots - sample.online)
                        text = (f"👥 <b>{SERVERS[sample.server_id]} · свободные слоты!</b>\n"
                                f"Онлайн ниже <b>{threshold}</b>: "
                                f"<b>{sample.online} / {sample.max_slots}</b>\n"
                                f"Свободно: {free}\n🕒 {when(sample.source_at)}")
                        await distribute(bot, app, [chat_id], text,
                                         unique=(f"slots:{sample.server_id}:{threshold}", dt_str(sample.source_at)))
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
                    image_bytes = None
                    try:
                        figures = {sid: app.db.stats(sid, start, end) for sid in SERVERS}
                        image_bytes = await asyncio.to_thread(render_daily_report_png, figures, "ЕЖЕДНЕВНЫЙ ОТЧЁТ · МСК")
                    except Exception:
                        log.exception("Cannot create daily report image, falling back to text")
                    for chat_id in report_chats:
                        if app.db.was_sent(chat_id, "daily", bucket):
                            continue
                        delivered = False
                        if image_bytes:
                            try:
                                await bot.send_photo(chat_id, BufferedInputFile(image_bytes, filename="checkrp_daily.png"),
                                                     caption="📅 <b>CheckRP · ежедневный отчёт</b>\nДанные за сегодня по МСК. Подробности: /report")
                                delivered = True
                            except TelegramForbiddenError:
                                app.db.disable(chat_id)
                            except (TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter):
                                log.warning("Report image delivery failed for chat %s", chat_id)
                        if not delivered:
                            delivered = await send(bot, app, chat_id, summary)
                        if delivered:
                            app.db.mark_sent(chat_id, "daily", bucket)
                        await asyncio.sleep(0.055)
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
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "CheckRP/4.5"}) as session:
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
                BotCommand(command="check", description="🌵 Yuma и Vice City"),
                BotCommand(command="notify", description="🔔 Настройки уведомлений"),
                BotCommand(command="stats", description="📊 Аналитика и графики"),
                BotCommand(command="events", description="📋 История отключений"),
                BotCommand(command="compare", description="⚖️ Сравнение серверов"),
                BotCommand(command="report", description="📅 Отчёты и PNG"),
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
