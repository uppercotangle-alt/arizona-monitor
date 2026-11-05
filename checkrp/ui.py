"""Telegram HTML and inline keyboards. Values are escaped where needed."""
from __future__ import annotations
from datetime import datetime, timedelta
from html import escape
from aiogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup
from .core import MAX_DATA_AGE, SLOT_LIMIT, TZ, UTC, SERVERS, Sample, Snapshot, next_payday
from .db import DB, Event, Preference


def B(text: str, cb: str) -> Btn:
    return Btn(text=text, callback_data=cb)


def kb_home(pref: Preference | None = None) -> Markup:
    notify_label = "🔔 Уведомления" if not (pref and pref.enabled) else "⚙️ Уведомления · ВКЛ"
    return Markup(inline_keyboard=[
        [B("🌵 Yuma", "s:25"), B("🏜 Show Low", "s:52")],
        [B("🔄 Обновить", "home"), B("📈 Графики", "charts")],
        [B("📋 События", "events"), B(notify_label, "settings")],
        [B("📅 Отчёт", "report"), B("ℹ️ О боте", "help")],
    ])


def kb_back(refresh: str | None = None) -> Markup:
    buttons = [[B("🔄 Обновить", refresh)]] if refresh else []
    buttons.append([B("⬅️ На главную", "home")])
    return Markup(inline_keyboard=buttons)


def kb_charts() -> Markup:
    return Markup(inline_keyboard=[
        [B("🌵 Yuma · 1 час", "g:25:1h"), B("🏜 Show Low · 1 час", "g:52:1h")],
        [B("🌵 Yuma · 24 ч", "g:25:24h"), B("🏜 Show Low · 24 ч", "g:52:24h")],
        [B("🌵 Yuma · 7 дней", "g:25:7d"), B("🏜 Show Low · 7 дней", "g:52:7d")],
        [B("⬅️ На главную", "home")],
    ])


def kb_settings(pref: Preference) -> Markup:
    flag = lambda val: "✅" if val else "▫️"
    rows = [[B("🔕 Отключить всё" if pref.enabled else "🔔 Включить подписку", "toggle:enabled")]]
    if pref.enabled:
        rows += [
            [B(f"{flag(pref.status_25)} Рестарты Yuma", "toggle:status_25"),
             B(f"{flag(pref.status_52)} Show Low", "toggle:status_52")],
            [B(f"{flag(pref.slots_25)} Слоты Yuma <{SLOT_LIMIT}", "toggle:slots_25")],
            [B(f"{flag(pref.slots_52)} Слоты Show Low <{SLOT_LIMIT}", "toggle:slots_52")],
            [B(f"{flag(pref.payday)} Пейдей · -10 минут", "toggle:payday")],
            [B(f"{flag(pref.daily)} Отчёт · 21:00 МСК", "toggle:daily")],
        ]
    rows.append([B("⬅️ На главную", "home")])
    return Markup(inline_keyboard=rows)


def when(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%d.%m.%Y %H:%M:%S МСК")


def age_text(sample: Sample, now: datetime) -> str:
    seconds = max(0, int((now - sample.source_at).total_seconds()))
    if seconds < 60:
        return f"{seconds} сек."
    return f"{seconds // 60} мин."


def progress(online: int, max_slots: int, length: int = 12) -> str:
    pct = min(1, max(0, online / max(1, max_slots)))
    return "▰" * round(pct * length) + "▱" * (length - round(pct * length))


def delta_text(db: DB, sample: Sample) -> str:
    d = db.last_10min_delta(sample.server_id, sample)
    if d is None:
        return "— (накапливаем историю)"
    number = d[0]
    if number > 0:
        return f"📈 +{number} за 10 мин."
    if number < 0:
        return f"📉 {number} за 10 мин."
    return "➖ 0 за 10 мин."


def one_server(db: DB, sample: Sample, now: datetime) -> str:
    title = f"🌵 <b>Yuma</b>" if sample.server_id == 25 else "🏜 <b>Show Low</b>"
    if not sample.fresh(now):
        return title + f"\n⚪ <b>Данные устарели</b> · {age_text(sample, now)}"
    if not sample.responding:
        return title + f"\n🔴 <b>Нет ответа</b> по данным API\n🕓 Источник: {when(sample.source_at)}"
    pct = 100 * sample.online / max(sample.max_slots, 1)
    return (f"{title}\n🟢 <b>Отвечает</b>\n"
            f"👥 <b>{sample.online:,} / {sample.max_slots:,}</b> · {pct:.1f}%\n"
            f"<code>{progress(sample.online, sample.max_slots)}</code>\n"
            f"{delta_text(db, sample)}\n"
            f"🕒 Данные: {age_text(sample, now)} назад").replace(",", " ")


def dashboard(db: DB, snapshot: Snapshot | None, api_error: str | None = None) -> str:
    now = datetime.now(UTC)
    parts = ["🌵 <b>CHECKRP · LIVE MONITOR</b>",
             "<i>Arizona RP · Yuma & Show Low</i>", "━━━━━━━━━━━━━━━━━━━━"]
    if snapshot:
        for sid in SERVERS:
            parts += [one_server(db, snapshot.servers[sid], now), ""]
        parts += ["━━━━━━━━━━━━━━━━━━━━", f"🕒 Проверено: {when(snapshot.fetched_at)}",
                  "🌐 Данные: g-rp.me · HTTP API"]
        if snapshot.source_problem:
            parts.append("⚠️ Много серверов одновременно без ответа: возможен сбой источника")
        if api_error:
            parts.append("⚠️ API недоступен; показаны последние сохранённые данные")
    else:
        parts.append("⚠️ Нет данных API. Повтори проверку позже.")
    local_now = now.astimezone(TZ)
    upcoming = next_payday(now)
    minutes = max(0, int((upcoming - local_now).total_seconds() // 60) + 1)
    parts.append(f"💰 Пейдей: {upcoming.strftime('%H:%M')} МСК · через ~{minutes} мин.")
    return "\n".join(parts)


def server_details(db: DB, snapshot: Snapshot | None, sid: int, api_error: str | None) -> str:
    if snapshot is None:
        return "⚠️ Сведения о сервере пока недоступны."
    sample = snapshot.servers[sid]
    now = datetime.now(UTC)
    text = ["🔎 <b>ДЕТАЛИ СЕРВЕРА</b>", "━━━━━━━━━━━━━━━━━━━━", one_server(db, sample, now), "",
            f"📈 Пик за час: <b>{sample.peak_hour:,}</b>",
            f"🏆 Пик за день: <b>{sample.peak_day:,}</b>",
            f"🕓 Обновлено источником: {when(sample.source_at)}",
            "", "<i>Источник: HTTP API. Рестарт определяется по потере/возврату ответа.</i>"]
    if api_error:
        text.append("⚠️ Не удалось получить новые данные")
    return "\n".join(text).replace(",", " ")


def event_text(event: Event) -> str:
    name = SERVERS[event.server_id]
    if event.kind == "down":
        return (f"🔴 <b>{escape(name)} — ответ пропал</b>\n"
                f"🕒 {when(event.at)}\n"
                "Возможный рестарт или временная недоступность. Причина неизвестна.")
    duration = (f"⏱️ Не отвечал около {max(1, round(event.duration_seconds / 60))} мин.\n"
                if event.duration_seconds is not None else "")
    return (f"🟢 <b>{escape(name)} — ответ вернулся</b>\n"
            f"🕒 {when(event.at)}\n{duration}"
            "Возможный рестарт завершился; причина отключения неизвестна.")


def events_text(events: list[Event]) -> str:
    if not events:
        return "📋 <b>ПОСЛЕДНИЕ СОБЫТИЯ</b>\n\nПока нет подтверждённых изменений статуса."
    lines = ["📋 <b>ПОСЛЕДНИЕ СОБЫТИЯ</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for e in events:
        lines.append(("🟢" if e.kind == "up" else "🔴") +
                     f" <b>{SERVERS[e.server_id]}</b> · " +
                     ("ответ вернулся" if e.kind == "up" else "ответ пропал") +
                     "\n" + when(e.at) +
                     (f" · около {max(1, round(e.duration_seconds / 60))} мин." if e.duration_seconds is not None else ""))
    lines.append("\n<i>Причины отключений API не раскрывает.</i>")
    return "\n\n".join(lines)


def settings_text(pref: Preference) -> str:
    if not pref.enabled:
        return ("🔔 <b>НАСТРОЙКИ УВЕДОМЛЕНИЙ</b>\n\n"
                "Подписка отключена. Нажми «Включить подписку» и выбери нужные оповещения.\n\n"
                "🕒 Весь график уведомлений — по Москве (МСК).")
    return ("⚙️ <b>НАСТРОЙКИ УВЕДОМЛЕНИЙ</b>\n\n"
            "✅ — включено, ▫️ — выключено.\n"
            "🔴 Рестарты: подтверждённая потеря и восстановление ответа.\n"
            f"👥 Слоты: уведомление при онлайне ниже <b>{SLOT_LIMIT}</b>, без повторного спама.\n"
            "💰 Пейдей: в <b>XX:20 и XX:50</b> МСК, за 10 минут до :30 и :00.\n"
            "📅 Отчёт: ежедневно около 21:00 МСК.\n\n"
            "<i>Пейдей и отчёты включаются отдельно, чтобы не засорять чат.</i>")


def stats_text(db: DB, since: datetime, until: datetime, label: str) -> str:
    lines = [f"📊 <b>ОТЧЁТ · {escape(label)}</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for sid, name in SERVERS.items():
        s = db.stats(sid, since, until)
        if s["n"] == 0:
            lines += [f"\n<b>{name}</b>: недостаточно данных"]
            continue
        fraction = round(100 * s["answered"] / s["n"], 1)
        average = f"{float(s['avg_online']):.0f}" if s["avg_online"] is not None else "—"
        max_online = s["peak"] if s["peak"] is not None else "—"
        min_online = s["minimum"] if s["minimum"] is not None else "—"
        downtime = round(int(s["downtime_seconds"]) / 60)
        longest = round(int(s["longest_downtime_seconds"]) / 60)
        lines += [f"\n<b>{name}</b>",
                  f"👥 Средний онлайн: <b>{average}</b>",
                  f"🏆 Пик: {max_online} · Мин.: {min_online}",
                  f"🔴 Зафиксировано отключений: {s['downs']}",
                  f"⏱️ Недоступность: ~{downtime} мин. · максимум ~{longest} мин.",
                  f"🟢 Доля ответов в измерениях: {fraction}%",
                  f"📍 Измерений: {s['n']}"]
    lines += ["", f"🕓 По МСК, до {when(until)}",
              "<i>Статистика по накопленным данным API, не гарантированное фактическое время работы.</i>"]
    return "\n".join(lines)


HELP = ("ℹ️ <b>CHECKRP · ПОМОЩЬ</b>\n\n"
        "/check — монитор серверов\n"
        "/notify — уведомления\n"
        "/stats — графики\n"
        "/events — журнал событий\n"
        "/report — статистика за сегодня\n"
        "/player — поиск игрока (пока не поддерживается)\n\n"
        "🌐 Источник: https://online.g-rp.me/api/v1/projects/arizona-rp\n"
        "🕒 Время: Москва (UTC+3).\n\n"
        "API отдаёт количество игроков, но не список ников/ID. "
        "Данные обновляются источником с задержкой; опрос каждые 20 секунд "
        "не означает обновления статистики каждые 20 секунд.")

PLAYER_NOTICE = ("🔎 <b>ПОИСК ИГРОКА</b>\n\n"
                 "У API g-rp.me нет списка персонажей, ников и текущих игровых ID. "
                 "Поэтому определить, находится ли Nick_Name в игре, по этому API <b>нельзя</b>.\n\n"
                 "Функцию подключим, когда появится доступный разрешённый источник списка игроков. "
                 "Не будем показывать выдуманный статус «не в сети».")
