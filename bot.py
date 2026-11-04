#!/usr/bin/env python3
"""Arizona RP Yuma / Show Low monitor. Python 3.10+, aiogram 3.x. HTTP only."""
from __future__ import annotations

import asyncio
import logging
from html import escape
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from monitor.api import APIClient, APIError, Snapshot
from monitor.config import Config, SERVERS
from monitor.logic import Transition, TransitionTracker
from monitor.storage import Storage
from monitor.ui import ABOUT, dashboard, details, event_message, history, keyboard

log = logging.getLogger("arizona_monitor")
router = Router()
UTC = timezone.utc


class UserLimiter:
    """Simple per-process anti-spam; API also has a shared fetch interval."""

    def __init__(self) -> None:
        self.last: dict[int, float] = {}

    def allow(self, user_id: int, every_seconds: float = 2.5) -> bool:
        now = time.monotonic()
        previous = self.last.get(user_id, -1e20)
        if now - previous < every_seconds:
            return False
        self.last[user_id] = now
        if len(self.last) > 5000:
            cutoff = now - 600
            self.last = {key: value for key, value in self.last.items() if value >= cutoff}
        return True


@dataclass(slots=True)
class Services:
    config: Config
    api: APIClient
    db: Storage
    tracker: TransitionTracker
    limiter: UserLimiter = field(default_factory=UserLimiter)


async def get_snapshot(svc: Services) -> tuple[Snapshot | None, str | None]:
    try:
        return await svc.api.fetch(), None
    except APIError:
        return svc.api.last_snapshot, "API error"


async def send_dashboard(message: Message, svc: Services) -> None:
    snap, error = await get_snapshot(svc)
    is_private = message.chat.type == "private"
    subscribed = is_private and await svc.db.subscribed(message.chat.id)
    await message.answer(
        dashboard(snap, svc.config.tz, svc.config.max_data_age_seconds, error=error),
        reply_markup=keyboard(subscribed=subscribed, private=is_private),
        disable_web_page_preview=True,
    )


async def edit_or_reply(call: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    if not isinstance(call.message, Message):
        return
    try:
        await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        # Editing identical content is not an error (e.g. rapid refresh of same API sample).
        if "message is not modified" in str(exc).lower():
            return
        log.warning("Failed to edit message: %s", exc)
        try:
            await call.message.answer(text, reply_markup=markup, disable_web_page_preview=True)
        except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError):
            log.exception("Unable to send callback result")


def _back() -> InlineKeyboardMarkup:
    return keyboard(subscribed=False, private=False, back=True)


@router.message(CommandStart())
async def cmd_start(message: Message, services: Services) -> None:
    await send_dashboard(message, services)


@router.message(Command("check"))
async def cmd_check(message: Message, services: Services) -> None:
    user = message.from_user
    if user is not None and not services.limiter.allow(user.id):
        await message.answer("⏳ Слишком часто. Повтори через пару секунд.")
        return
    await send_dashboard(message, services)


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, services: Services) -> None:
    if message.chat.type != "private":
        await message.answer("🔐 Подписка доступна только в личном чате с ботом.")
        return
    await services.db.subscribe(message.chat.id)
    await message.answer("🔔 <b>Уведомления включены.</b> Сообщу об исчезновении и восстановлении ответа серверов.")


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message, services: Services) -> None:
    if message.chat.type != "private":
        await message.answer("🔐 Управлять подпиской можно в личном чате.")
        return
    await services.db.unsubscribe(message.chat.id)
    await message.answer("🔕 Уведомления отключены. Команда /check продолжит работать.")


@router.message(Command("events"))
async def cmd_events(message: Message, services: Services) -> None:
    rows = await services.db.recent_events()
    await message.answer(history(rows, services.config.tz), reply_markup=_back())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(ABOUT, reply_markup=_back(), disable_web_page_preview=True)


@router.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    await message.answer("🏓 Pong! Бот принимает команды. Состояние серверов: /check")


@router.message(Command("health"))
async def cmd_health(message: Message, services: Services) -> None:
    if services.config.admin_id == 0 or message.from_user is None or message.from_user.id != services.config.admin_id:
        await message.answer("⛔ Недоступно.")
        return
    count = await services.db.count_subscribers()
    last = services.api.last_snapshot
    last_at = last.fetched_at.astimezone(services.config.tz).strftime("%d.%m %H:%M:%S") if last else "нет"
    err = services.api.last_error or "нет"
    await message.answer(
        "🛠 <b>ДИАГНОСТИКА</b>\n"
        f"Подписчиков: {count}\n"
        f"Последний успешный HTTP-запрос: {last_at}\n"
        f"Последняя ошибка API: {escape(err[:180])}\n"
        f"Интервал опроса: {services.config.poll_seconds} сек."
    )


@router.callback_query(F.data == "check")
async def cb_check(call: CallbackQuery, services: Services) -> None:
    if not services.limiter.allow(call.from_user.id):
        await call.answer("⏳ Подожди пару секунд", show_alert=False)
        return
    await call.answer("🔄 Проверяю API…")
    snap, error = await get_snapshot(services)
    if not isinstance(call.message, Message):
        return
    is_private = call.message.chat.type == "private"
    sub = is_private and await services.db.subscribed(call.message.chat.id)
    await edit_or_reply(call, dashboard(snap, services.config.tz,
                                       services.config.max_data_age_seconds, error=error),
                        keyboard(subscribed=sub, private=is_private))


@router.callback_query(F.data.in_({"server:25", "server:52"}))
async def cb_server(call: CallbackQuery, services: Services) -> None:
    if not services.limiter.allow(call.from_user.id):
        await call.answer("⏳ Подожди пару секунд")
        return
    await call.answer("🔎 Проверяю…")
    sid = int((call.data or "server:25").split(":", 1)[1])
    snap, error = await get_snapshot(services)
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"server:{sid}")],
        [InlineKeyboardButton(text="⬅️ К монитору", callback_data="check")],
    ])
    await edit_or_reply(call, details(snap, sid, services.config.tz,
                                     services.config.max_data_age_seconds, error=error), markup)


@router.callback_query(F.data.in_({"notify:on", "notify:off"}))
async def cb_notify(call: CallbackQuery, services: Services) -> None:
    if not isinstance(call.message, Message) or call.message.chat.type != "private":
        await call.answer("Подписка доступна только в личном чате", show_alert=True)
        return
    if call.data == "notify:on":
        await services.db.subscribe(call.message.chat.id)
        await call.answer("🔔 Уведомления включены", show_alert=True)
    else:
        await services.db.unsubscribe(call.message.chat.id)
        await call.answer("🔕 Уведомления отключены", show_alert=True)
    snap, error = await get_snapshot(services)
    subscribed = await services.db.subscribed(call.message.chat.id)
    await edit_or_reply(call, dashboard(snap, services.config.tz,
                                       services.config.max_data_age_seconds, error=error),
                        keyboard(subscribed=subscribed, private=True))


@router.callback_query(F.data == "events")
async def cb_events(call: CallbackQuery, services: Services) -> None:
    await call.answer()
    rows = await services.db.recent_events()
    await edit_or_reply(call, history(rows, services.config.tz), _back())


@router.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery) -> None:
    await call.answer()
    await edit_or_reply(call, ABOUT, _back())


async def _send_event(bot: Bot, chat_id: int, text: str, services: Services) -> None:
    try:
        await bot.send_message(chat_id, text, disable_web_page_preview=True)
    except TelegramForbiddenError:
        log.info("Subscriber %s blocked the bot; removing", chat_id)
        await services.db.unsubscribe(chat_id)
    except TelegramRetryAfter as exc:
        delay = min(float(exc.retry_after), 30.0)
        log.warning("Telegram rate limit; retrying after %.1fs", delay)
        await asyncio.sleep(delay)
        try:
            await bot.send_message(chat_id, text, disable_web_page_preview=True)
        except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter):
            log.warning("Could not deliver notification after retry to %s", chat_id)
    except (TelegramBadRequest, TelegramNetworkError):
        log.warning("Unable to notify subscriber %s", chat_id)


async def notify_subscribers(bot: Bot, event: Transition, services: Services) -> None:
    subscribers = await services.db.all_subscribers()
    if not subscribers:
        return
    text = event_message(event, services.config.tz)
    for chat_id in subscribers:
        await _send_event(bot, chat_id, text, services)
        await asyncio.sleep(0.04)  # avoid bursts and Telegram flood limits


async def monitor_loop(bot: Bot, services: Services) -> None:
    log.info("Monitor started: HTTP API every %ss", services.config.poll_seconds)
    while True:
        try:
            snap = await services.api.fetch()
            if snap.probable_source_outage:
                log.warning("Upstream reports unusually many servers offline; suppressing alerts")
            events = services.tracker.update(snap, datetime.now(UTC))
            for event in events:
                await services.db.save_event(event)
                log.warning("Confirmed status transition: %s -> %s", event.name, event.kind)
                await notify_subscribers(bot, event, services)
        except APIError:
            # No claims about server state when HTTP API fails.
            pass
        except Exception:
            log.exception("Unexpected monitoring error; next iteration will retry")
        await asyncio.sleep(services.config.poll_seconds)


async def main() -> None:
    config = Config.from_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    timeout = aiohttp.ClientTimeout(total=12, connect=5)
    connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=120)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector,
                                     headers={"User-Agent": "ArizonaRPMonitor/2.0"}) as session:
        db = Storage(config.db_path)
        await db.open()
        services = Services(
            config=config,
            api=APIClient(session),
            db=db,
            tracker=TransitionTracker(max_age_seconds=config.max_data_age_seconds),
        )
        bot = Bot(config.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dp = Dispatcher()
        dp.include_router(router)
        dp["services"] = services
        task: asyncio.Task[None] | None = None
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            await bot.set_my_commands([
                BotCommand(command="start", description="🌵 Открыть монитор"),
                BotCommand(command="check", description="📡 Проверить Yuma и Show Low"),
                BotCommand(command="subscribe", description="🔔 Включить оповещения"),
                BotCommand(command="unsubscribe", description="🔕 Отключить оповещения"),
                BotCommand(command="events", description="📋 Последние события"),
                BotCommand(command="help", description="ℹ️ Помощь"),
            ])
            task = asyncio.create_task(monitor_loop(bot, services), name="arizona_monitor")
            log.info("Bot polling started, following server IDs %s", list(SERVERS))
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(),
                                   close_bot_session=False)
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await bot.session.close()
            await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    except ValueError as exc:
        # Config errors should be visible, but never print secrets.
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1)
