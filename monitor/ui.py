"""Telegram HTML formatting and inline keyboard helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .api import ServerInfo, Snapshot
from .config import API_URL, SERVERS
from .logic import Transition
from .storage import EventRow

UTC = timezone.utc


def keyboard(*, subscribed: bool, private: bool, back: bool = False) -> InlineKeyboardMarkup:
    if back:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К монитору", callback_data="check")],
        ])
    rows = [
        [InlineKeyboardButton(text="🌵 Yuma", callback_data="server:25"),
         InlineKeyboardButton(text="🏜 Show Low", callback_data="server:52")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="check")],
    ]
    if private:
        rows.append([InlineKeyboardButton(
            text="🔕 Отключить уведомления" if subscribed else "🔔 Включить уведомления",
            callback_data="notify:off" if subscribed else "notify:on",
        )])
    rows.append([InlineKeyboardButton(text="📋 События", callback_data="events"),
                 InlineKeyboardButton(text="ℹ️ О боте", callback_data="help")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _time(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%d.%m.%Y %H:%M:%S")


def _age(ts: datetime, now: datetime) -> str:
    delta = max(0, round((now - ts).total_seconds()))
    if delta < 60:
        return f"{delta} сек."
    if delta < 3600:
        return f"{delta // 60} мин. {delta % 60} сек."
    return f"{delta // 3600} ч. {(delta % 3600) // 60} мин."


def _bar(n: int, maximum: int, width: int = 10) -> str:
    fill = min(width, round(width * n / maximum)) if maximum else 0
    return "▰" * fill + "▱" * (width - fill)


def _server_card(sid: int, info: ServerInfo | None, now: datetime, tz: ZoneInfo,
                 max_age: int, *, detailed: bool = False,
                 source_unavailable: bool = False) -> str:
    name = SERVERS[sid]
    emoji = "🌵" if sid == 25 else "🏜"
    if info is None:
        return f"{emoji} <b>{name}</b>\n⚪ Нет данных об этом сервере в API"
    age = info.freshness_seconds(now)
    if source_unavailable:
        return (f"{emoji} <b>{name}</b>\n"
                f"⚪ Текущий статус неизвестен (нет доступа к API)\n"
                f"👥 Последний онлайн: {info.online} / {info.max_slots}\n"
                f"🕒 Последний источник: {_time(info.updated_at, tz)}")
    if not info.is_fresh(now, max_age):
        return (f"{emoji} <b>{name}</b>\n"
                f"🟠 Данные API устарели или имеют неверное время\n"
                f"🕒 Источник: {_time(info.updated_at, tz)}\n"
                f"⚠️ Старый онлайн не выдаётся за текущий")
    status = "🟢 Отвечает" if info.responding else "🔴 Нет ответа (по данным API)"
    online = f"{info.online:,}".replace(",", " ")
    maximum = f"{info.max_slots:,}".replace(",", " ")
    percent = info.online / info.max_slots * 100
    lines = [
        f"{emoji} <b>{name}</b>",
        f"{status}",
        f"👥 <b>{online} / {maximum}</b>  ·  {percent:.1f}%",
        f"<code>{_bar(info.online, info.max_slots)}</code>",
    ]
    if detailed:
        if info.peak_hour is not None:
            lines.append(f"📈 Пик за час: <b>{info.peak_hour}</b>")
        if info.peak_day is not None:
            lines.append(f"🏆 Пик за день: <b>{info.peak_day}</b>")
        lines.append(f"🕒 Обновлено источником: {_time(info.updated_at, tz)}")
        lines.append(f"⌛ Возраст данных: {_age(info.updated_at, now)}")
    else:
        lines.append(f"🕒 Данные: {_age(info.updated_at, now)} назад")
    return "\n".join(lines)


def dashboard(snap: Snapshot | None, tz: ZoneInfo, max_age: int,
              *, error: str | None = None) -> str:
    now = datetime.now(UTC)
    lines = ["🌵 <b>ARIZONA RP · LIVE MONITOR</b>",
             "<i>Yuma  •  Show Low</i>", "━━━━━━━━━━━━━━━━━━━━"]
    if error:
        lines.append("⚠️ <b>Не удалось связаться с API.</b>")
        lines.append("Ниже — последний сохранённый снимок, он может быть неактуален." if snap
                     else "Текущий онлайн неизвестен. Повтори запрос позже.")
    if snap is not None:
        for sid in SERVERS:
            lines.append("")
            lines.append(_server_card(sid, snap.servers.get(sid), now, tz, max_age,
                                      source_unavailable=bool(error)))
        if snap.probable_source_outage:
            lines.extend(["", "⚠️ API одновременно показывает массовую недоступность серверов — уведомления приостановлены."])
        lines.extend(["", "━━━━━━━━━━━━━━━━━━━━",
                      f"🔄 Запрос к API: {_time(snap.fetched_at, tz)}"])
    lines.append("ℹ️ Источник: g-rp.me · без UDP")
    lines.append("<i>Время обновления API может отставать от игры.</i>")
    return "\n".join(lines)


def details(snap: Snapshot | None, sid: int, tz: ZoneInfo, max_age: int,
            *, error: str | None = None) -> str:
    now = datetime.now(UTC)
    title = "🔎 <b>ДЕТАЛИ СЕРВЕРА</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    if error and snap is None:
        return title + "⚠️ API не отвечает, данных нет."
    info = snap.servers.get(sid) if snap else None
    body = _server_card(sid, info, now, tz, max_age, detailed=True,
                        source_unavailable=bool(error))
    if error:
        body = "⚠️ API недоступен; отображается последний снимок.\n\n" + body
    return title + body + "\n\n<i>Статус по HTTP API, не прямое соединение с сервером.</i>"


def event_message(event: Transition, tz: ZoneInfo) -> str:
    if event.kind == "lost":
        return (f"🚨 <b>{escape(event.name)} — пропал ответ</b>\n\n"
                "По данным мониторинга сервер перестал отвечать.\n"
                "Это <b>может быть рестарт</b>, но причина не подтверждена.\n\n"
                f"🕒 Обнаружено: {_time(event.detected_at, tz)}\n"
                "<i>Событие подтверждено двумя разными обновлениями API.</i>")
    duration = ""
    if event.downtime_seconds is not None:
        minutes, seconds = divmod(event.downtime_seconds, 60)
        duration = f"\n⏱ Отсутствие ответа: ~{minutes} мин. {seconds} сек."
    return (f"✅ <b>{escape(event.name)} — ответ восстановлен</b>\n\n"
            "API снова видит сервер доступным.\n"
            "Возможный рестарт или сбой закончился."
            f"{duration}\n\n🕒 Обнаружено: {_time(event.detected_at, tz)}")


def history(rows: list[EventRow], tz: ZoneInfo) -> str:
    lines = ["📋 <b>ПОСЛЕДНИЕ СОБЫТИЯ</b>", "━━━━━━━━━━━━━━━━━━━━"]
    if not rows:
        lines.append("\nСобытий пока нет. Мониторинг начнёт записывать изменения после запуска.")
    for r in rows:
        dt = datetime.fromisoformat(r.at)
        mark = "🔴" if r.kind == "lost" else "🟢"
        label = "Ответ пропал" if r.kind == "lost" else "Ответ вернулся"
        lines.append(f"\n{mark} <b>{escape(r.name)}</b> — {label}\n   {_time(dt, tz)}")
    lines.append("\n<i>Причина отключения неизвестна; это не подтверждение рестарта.</i>")
    return "\n".join(lines)


ABOUT = ("ℹ️ <b>ARIZONA RP MONITOR</b>\n\n"
         "Следим за Yuma и Show Low через HTTP API g-rp.me.\n\n"
         "<b>Команды:</b>\n"
         "/check — проверить серверы\n"
         "/subscribe — включить уведомления\n"
         "/unsubscribe — отключить уведомления\n"
         "/events — последние события\n"
         "/help — эта справка\n\n"
         "🔎 <b>Как определяем события:</b> два разных обновления источника должны "
         "подтвердить изменение поля responding. Устаревшие данные и ошибки API "
         "не превращаются в ложные рестарты.\n\n"
         "⚠️ API может показывать статус с задержкой. Никакого прямого UDP нет.\n"
         f"<a href=\"{API_URL}\">Открыть источник</a>")
