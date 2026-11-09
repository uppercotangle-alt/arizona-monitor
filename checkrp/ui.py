"""Telegram-native HTML messages and inline buttons (no Web App)."""
from __future__ import annotations
from datetime import datetime
from html import escape
from aiogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup
from .core import TZ, UTC, SERVERS, SERVER_ICONS, ARCHIVED_SERVERS, Sample, Snapshot, next_payday
from .db import DB, Event, Preference, ALLOWED_THRESHOLDS


def B(text: str, cb: str) -> Btn:
    return Btn(text=text, callback_data=cb)


def kb_home(pref: Preference | None = None) -> Markup:
    notify = "⚙️ Подписки · ВКЛ" if pref and pref.enabled else "🔔 Подписки"
    return Markup(inline_keyboard=[
        [B("🌵 Yuma", "s:25"), B("🌴 Vice City", "s:58")],
        [B("🔄 Обновить", "home"), B("📈 Графики", "charts")],
        [B("⚖️ Сравнение", "compare:30"), B(notify, "settings")],
        [B("📋 События", "events"), B("📅 Отчёт", "report")],
        [B("ℹ️ Справка", "help")],
    ])


def kb_back(refresh: str | None = None) -> Markup:
    rows = [[B("🔄 Обновить", refresh)]] if refresh else []
    rows.append([B("⬅️ К монитору", "home")])
    return Markup(inline_keyboard=rows)


def kb_events(archive: bool = False) -> Markup:
    return Markup(inline_keyboard=[
        [B("🕘 Текущие события" if archive else "📁 Архив Show Low", "events" if archive else "archive")],
        [B("🔄 Обновить", "archive" if archive else "events")],
        [B("⬅️ К монитору", "home")],
    ])


def kb_charts() -> Markup:
    rows = []
    for period, label in (("1h", "1 час"), ("24h", "24 ч"), ("7d", "7 дней")):
        rows.append([B(f"🌵 Yuma · {label}", f"g:25:{period}"),
                     B(f"🌴 Vice City · {label}", f"g:58:{period}")])
    rows.append([B("⚖️ График сравнения · 24 ч", "g:both:24h")])
    rows.append([B("⬅️ К монитору", "home")])
    return Markup(inline_keyboard=rows)


def kb_compare(period: int = 30) -> Markup:
    return Markup(inline_keyboard=[
        [B(("● " if period == t else "") + f"{t} мин", f"compare:{t}") for t in (10, 30, 60)],
        [B("📈 График за сутки", "g:both:24h")],
        [B("⬅️ К монитору", "home")],
    ])


def kb_settings(pref: Preference) -> Markup:
    flag = lambda v: "✅" if v else "▫️"
    rows = [[B("🔕 Отключить всё" if pref.enabled else "🔔 Включить подписку", "toggle:enabled")]]
    if pref.enabled:
        rows += [
            [B(f"{flag(pref.status_25)} Статус Yuma", "toggle:status_25"),
             B(f"{flag(pref.status_58)} Vice City", "toggle:status_58")],
            [B(f"{flag(pref.slots_25)} Слоты Yuma <{pref.threshold_25}", "toggle:slots_25")],
            [B(f"{flag(pref.slots_58)} Слоты Vice City <{pref.threshold_58}", "toggle:slots_58")],
            [B("🎛 Настроить пороги", "thresholds")],
            [B(f"{flag(pref.payday)} Пейдей · −10 минут", "toggle:payday")],
            [B(f"{flag(pref.daily)} Отчёт · 21:00 МСК", "toggle:daily")],
        ]
    rows.append([B("⬅️ К монитору", "home")])
    return Markup(inline_keyboard=rows)


def kb_thresholds(pref: Preference) -> Markup:
    rows = []
    for sid, name in SERVERS.items():
        current = pref.threshold_25 if sid == 25 else pref.threshold_58
        rows.append([B(f"{SERVER_ICONS[sid]} {name} — порог {current}", "thresholds")])
        rows.append([B(("✅ " if current == t else "") + str(t), f"limit:{sid}:{t}")
                     for t in ALLOWED_THRESHOLDS[sid]])
    rows.append([B("⬅️ К подпискам", "settings")])
    return Markup(inline_keyboard=rows)


def when(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%d.%m.%Y %H:%M:%S МСК")


def age_text(sample: Sample, now: datetime) -> str:
    age = max(0, int((now - sample.source_at).total_seconds()))
    return f"{age} сек." if age < 60 else f"{age // 60} мин."


def progress(online: int, max_slots: int, length: int = 10) -> str:
    filled = round(min(1, max(0, online / max(1, max_slots))) * length)
    return "▰" * filled + "▱" * (length-filled)


def delta_text(db: DB, sample: Sample, minutes: int = 10) -> str:
    change = db.delta(sample.server_id, sample, minutes)
    if change is None:
        return f"➖ {minutes} мин.: ждём историю"
    d = change[0]
    if d > 0:
        return f"📈 +{d} за {minutes} мин."
    if d < 0:
        return f"📉 {d} за {minutes} мин."
    return f"➖ Без изменений за {minutes} мин."


def one_server(db: DB, sample: Sample, now: datetime) -> str:
    title = f"{SERVER_ICONS[sample.server_id]} <b>{escape(sample.name.upper())}</b>"
    if not sample.fresh(now):
        return f"{title}\n⚪ <b>Данные устарели</b> · {age_text(sample, now)}"
    if not sample.responding:
        return f"{title}\n🔴 <b>Нет ответа</b> по данным API\n🕒 {when(sample.source_at)}"
    pct = 100 * sample.online / max(sample.max_slots, 1)
    free = max(0, sample.max_slots - sample.online)
    return (f"{title}\n🟢 Отвечает · <b>{sample.online:,} / {sample.max_slots:,}</b> · {pct:.1f}%\n"
            f"<code>{progress(sample.online, sample.max_slots)}</code>  ·  {free} свободно\n"
            f"{delta_text(db, sample)}\n"
            f"🕒 Данные: {age_text(sample, now)} назад").replace(",", " ")


def dashboard(db: DB, snapshot: Snapshot | None, api_error: str | None = None) -> str:
    now = datetime.now(UTC)
    parts = ["🌵 <b>CHECKRP · LIVE MONITOR</b>",
             "<i>Arizona Role Play · Yuma / Vice City</i>",
             "━━━━━━━━━━━━━━━━━━━━"]
    if snapshot:
        for sid in SERVERS:
            parts += [one_server(db, snapshot.servers[sid], now), ""]
        parts += ["━━━━━━━━━━━━━━━━━━━━", f"🕒 Проверено: {when(snapshot.fetched_at)}",
                  "🌐 g-rp.me · HTTP API"]
        if snapshot.source_problem:
            parts.append("⚠️ Массовая недоступность: возможна проблема источника")
        if api_error:
            parts.append("⚠️ API недоступен; последние известные данные (не live)")
    else:
        parts.append("⚠️ Нет актуальных данных API. Повтори проверку позже.")
    payday = next_payday(now)
    mins = max(0, int((payday - now).total_seconds() / 60) + 1)
    parts.append(f"💰 Следующий пейдей: {payday.strftime('%H:%M')} МСК · ~{mins} мин.")
    return "\n".join(parts)


def server_details(db: DB, snapshot: Snapshot | None, sid: int, api_error: str | None) -> str:
    if snapshot is None or sid not in snapshot.servers:
        return "⚠️ Сведения о сервере пока недоступны."
    sample = snapshot.servers[sid]
    now = datetime.now(UTC)
    result = [f"🔎 <b>{SERVER_ICONS[sid]} {SERVERS[sid].upper()} · ДЕТАЛИ</b>",
              "━━━━━━━━━━━━━━━━━━━━", one_server(db, sample, now), "",
              f"📈 Пик за час: <b>{sample.peak_hour:,}</b>",
              f"🏆 Пик за день: <b>{sample.peak_day:,}</b>",
              f"🕒 Обновлено источником: {when(sample.source_at)}",
              "", "<i>HTTP API не подтверждает причину отключений.</i>"]
    if api_error:
        result.append("⚠️ Свежие данные получить не удалось")
    return "\n".join(result).replace(",", " ")


def comparison_text(db: DB, snapshot: Snapshot | None, api_error: str | None, minutes: int = 30) -> str:
    if not snapshot:
        return "⚖️ <b>СРАВНЕНИЕ</b>\n\n⚠️ Пока нет данных API."
    now = datetime.now(UTC)
    lines = ["⚖️ <b>CHECKRP · СРАВНЕНИЕ</b>", f"<i>Изменение за {minutes} мин. · МСК</i>", "━━━━━━━━━━━━━━━━━━━━"]
    for sid in SERVERS:
        s = snapshot.servers[sid]
        lines += ["", f"{SERVER_ICONS[sid]} <b>{escape(s.name)}</b>"]
        if not s.fresh(now) or not s.responding:
            lines.append("⚪ Свежих данных нет")
            continue
        pct = 100 * s.online / max(s.max_slots, 1)
        lines.extend([f"👥 {s.online} / {s.max_slots} · <b>{pct:.1f}%</b>", delta_text(db, s, minutes)])
    lines.extend(["", "━━━━━━━━━━━━━━━━━━━━", "<i>Сравнение в процентах учитывает разное число слотов.</i>",
                  f"🕒 {when(snapshot.fetched_at)}"])
    if api_error:
        lines.append("⚠️ API недоступен — использованы сохранённые данные")
    return "\n".join(lines)


def event_text(event: Event) -> str:
    name = SERVERS.get(event.server_id, ARCHIVED_SERVERS.get(event.server_id, f"#{event.server_id}"))
    if event.kind == "down":
        return (f"🔴 <b>{escape(name)} · ответ пропал</b>\n"
                f"🕒 {when(event.at)}\n"
                "⚠️ Возможный рестарт или недоступность. Причина неизвестна.")
    duration = (f"⏱️ Длительность: ~{max(1, round(event.duration_seconds / 60))} мин.\n"
                if event.duration_seconds is not None else "")
    return (f"🟢 <b>{escape(name)} · ответ восстановлен</b>\n"
            f"🕒 {when(event.at)}\n{duration}"
            "<i>Причина недоступности неизвестна.</i>")


def events_text(events: list[Event], archive: bool = False) -> str:
    title = "📁 <b>АРХИВ SHOW LOW</b>" if archive else "📋 <b>СОБЫТИЯ · YUMA / VICE CITY</b>"
    if not events:
        return title + "\n\nПока событий нет."
    lines = [title, "━━━━━━━━━━━━━━━━━━━━"]
    for e in events:
        name = SERVERS.get(e.server_id, ARCHIVED_SERVERS.get(e.server_id, "Неизвестный сервер"))
        action = "Ответ восстановлен" if e.kind == "up" else "Ответ пропал"
        duration = f" · ~{max(1, round(e.duration_seconds / 60))} мин." if e.duration_seconds is not None else ""
        lines.append(f"{'🟢' if e.kind == 'up' else '🔴'} <b>{escape(name)}</b> · {action}\n{when(e.at)}{duration}")
    lines.append("<i>События фиксируются по изменениям responding в HTTP API.</i>")
    return "\n\n".join(lines)


def settings_text(pref: Preference) -> str:
    if not pref.enabled:
        return ("🔔 <b>CHECKRP · ПОДПИСКИ</b>\n\n"
                "🔕 Уведомления отключены. Включи подписку и выбери события.\n"
                "🕒 Все расписания — по Москве (МСК).")
    enabled = lambda b: "✅" if b else "▫️"
    return ("⚙️ <b>CHECKRP · ПОДПИСКИ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🌵 Статус Yuma: {enabled(pref.status_25)}\n"
            f"🌴 Статус Vice City: {enabled(pref.status_58)}\n"
            f"👥 Слоты Yuma &lt; {pref.threshold_25}: {enabled(pref.slots_25)}\n"
            f"👥 Слоты Vice City &lt; {pref.threshold_58}: {enabled(pref.slots_58)}\n"
            f"💰 Пейдей −10 мин.: {enabled(pref.payday)}\n"
            f"📅 Отчёт в 21:00 МСК: {enabled(pref.daily)}\n\n"
            "<i>Рестарты — возможные, по API. Настройки каждого сервера независимы.</i>")


def stats_text(db: DB, since: datetime, until: datetime, label: str) -> str:
    lines = [f"📊 <b>CHECKRP · {escape(label)}</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for sid, name in SERVERS.items():
        s = db.stats(sid, since, until)
        lines.append(f"\n{SERVER_ICONS[sid]} <b>{name}</b>")
        if not s["n"]:
            lines.append("⌛ Пока нет истории за этот период")
            continue
        average = f"{float(s['avg_online']):.0f}" if s["avg_online"] is not None else "—"
        peak = s["peak"] if s["peak"] is not None else "—"
        minimum = s["minimum"] if s["minimum"] is not None else "—"
        count = max(1, int(s["n"]))
        coverage = 100 * int(s["answered"] or 0) / count
        lines += [f"👥 Средний: <b>{average}</b> · пик: <b>{peak}</b> · мин: {minimum}",
                  f"🔴 Потерь ответа: {s['downs']}",
                  f"⏱️ Недоступность: ~{round(int(s['downtime_seconds']) / 60)} мин.",
                  f"🟢 Ответов в выборке: {coverage:.1f}% · измерений: {count}"]
    lines += ["", f"🕒 {when(until)}",
              "<i>По накопленным данным HTTP API; не гарантированный аптайм игры.</i>"]
    return "\n".join(lines)


HELP = ("ℹ️ <b>CHECKRP v4 · ПОМОЩЬ</b>\n\n"
        "/check — онлайн Yuma и Vice City\n"
        "/notify — настройка уведомлений и порогов\n"
        "/stats — PNG-графики\n"
        "/compare — сравнение за 10/30/60 мин.\n"
        "/events — события и архив Show Low\n"
        "/report — отчёт за сегодня\n"
        "/player — о поиске игроков\n"
        "/health — диагностика (администратор)\n\n"
        "🌐 https://online.g-rp.me/api/v1/projects/arizona-rp\n"
        "🕒 Время: МСК · HTTP, без UDP.\n"
        "<i>Опрос API не означает, что источник обновляется так же часто.</i>")

PLAYER_NOTICE = ("🔎 <b>ПОИСК ИГРОКА</b>\n\n"
                 "Источник g-rp.me не отдаёт список ников и игровых ID. "
                 "Поэтому проверить присутствие конкретного игрока по нему нельзя. "
                 "Функция появится только с доступным разрешённым источником данных.")
