#!/usr/bin/env python3
"""CheckRP v4 — Yuma / Vice City Telegram native monitor, HTTP API only.

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
                        comparison_text, server_details, settings_text, stats_text, when)

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
async def stats(message: Message) -> None:
    await message.answer("📈 <b>CHECKRP · ГРАФИКИ</b>\nВыбери сервер и период.\n\n"
                         "История Vice City накапливается с обновления v4.",
                         reply_markup=kb_charts())


@router.message(Command("report"))
async def report(message: Message, app: App) -> None:
    now = datetime.now(UTC)
    start = now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    await message.answer(stats_text(app.db, start, now, "СЕГОДНЯ"), reply_markup=kb_back("report"))
    try:
        figures = {sid: app.db.stats(sid, start, now) for sid in SERVERS}
        png = await asyncio.to_thread(render_daily_report_png, figures, "СЕГОДНЯ · МСК")
        await message.answer_photo(BufferedInputFile(png, filename="checkrp_daily.png"))
    except Exception:
        log.exception("Cannot render requested report image")


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
    if data.startswith(("toggle:", "limit:")) or data in ("settings", "thresholds"):
        if not private:
            await call.answer("Настройки только в личном чате", show_alert=True)
            return
        if data.startswith("limit:"):
            try:
                _, sid, chosen = data.split(":")
                pref = app.db.set_threshold(chat_id, int(sid), int(chosen))
            except (ValueError, TypeError):
                await call.answer("Недопустимый порог", show_alert=True)
                return
        elif data.startswith("toggle:"):
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
        await call.answer("Сохранено" if data.startswith(("toggle:", "limit:")) else "")
        if data == "thresholds" or data.startswith("limit:"):
            await edit(call, "🎛 <b>ПОРОГИ ОНЛАЙНА</b>\nВыбери порог для каждого сервера.\nПосле изменения настройка применится при следующем обновлении.", kb_thresholds(pref))
        else:
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
    elif data.startswith("s:") and data[2:].isdigit() and int(data[2:]) in SERVERS:
        await call.answer("🔎 Проверяю")
        sid = int(data.split(":")[1])
        snap, err = await latest(app)
        await edit(call, server_details(app.db, snap, sid, err), kb_back(data))
    elif data == "events":
        await call.answer()
        await edit(call, events_text(app.db.recent_events()), kb_events())
    elif data == "archive":
        await call.answer()
        await edit(call, events_text(app.db.recent_events(archive=True), archive=True), kb_events(archive=True))
    elif data.startswith("compare:"):
        try:
            period = int(data.split(":", 1)[1])
        except ValueError:
            period = -1
        if period not in (10, 30, 60):
            await call.answer("Неизвестный период")
            return
        await call.answer("⚖️ Сравниваю")
        snap, err = await latest(app)
        await edit(call, comparison_text(app.db, snap, err, period), kb_compare(period))
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
        await edit(call, "📈 <b>CHECKRP · ГРАФИКИ</b>\nВыбери сервер и период.\nЕсть и сравнение по заполненности.", kb_charts())
    elif data.startswith("g:"):
        parts = data.split(":")
        if len(parts) != 3 or parts[1] not in ({str(x) for x in SERVERS} | {"both"}) or parts[2] not in PERIODS:
            await call.answer("Неизвестный период")
            return
        period = parts[2]
        now = datetime.now(UTC)
        since = now - PERIODS[period]
        await call.answer("📊 Рисую график")
        if not isinstance(message, Message):
            return
        try:
            if parts[1] == "both":
                series = {sid: app.db.points(sid, since) for sid in SERVERS}
                limits = {sid: (app.api.latest.servers[sid].max_slots if app.api.latest else (1000 if sid == 25 else 750))
                          for sid in SERVERS}
                png = await asyncio.to_thread(render_comparison_png, series, period, limits)
                if png is None:
                    await message.answer("⌛ Ещё мало истории для сравнения.")
                    return
                caption = ("⚖️ <b>Yuma × Vice City</b>\n"
                           "Заполненность в % от вместимости каждого сервера.\n"
                           "<i>Данные g-rp.me · МСК</i>")
                filename = f"comparison_{period}.png"
            else:
                sid = int(parts[1])
                points = app.db.points(sid, since)
                if len(points) < 2:
                    await message.answer("⌛ Ещё мало истории для графика. Дай боту накопить данные.")
                    return
                capacity = app.api.latest.servers[sid].max_slots if app.api.latest else (1000 if sid == 25 else 750)
                png = await asyncio.to_thread(render_png, points, SERVERS[sid], period, capacity)
                if png is None:
                    return
                stat = app.db.stats(sid, since, now)
                avg = f"{float(stat['avg_online']):.0f}" if stat["avg_online"] is not None else "—"
                caption = (f"📈 <b>{SERVERS[sid]} · {period}</b>\n"
                           f"Средний онлайн: {avg} · Пик: {stat['peak'] if stat['peak'] is not None else '—'}\n"
                           f"Измерений: {stat['n']}\n<i>HTTP API · МСК</i>")
                filename = f"{sid}_{period}.png"
            await message.answer_photo(BufferedInputFile(png, filename=filename), caption=caption,
                                       reply_markup=kb_charts())
        except Exception:
            log.exception("Unable to render graph")
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
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "CheckRP/4.0"}) as session:
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
                BotCommand(command="stats", description="📈 Графики онлайна"),
                BotCommand(command="events", description="📋 История отключений"),
                BotCommand(command="compare", description="⚖️ Сравнение серверов"),
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
