"""CheckRP 4.5 Telegram-native navigation: one editable message, no Web App.

The 'screens' are regular Telegram HTML messages and InlineKeyboardMarkup.
Callback data is deliberately short (<64 bytes) and self-contained.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from aiogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup

from .core import TZ, UTC, SERVERS, SERVER_ICONS, ARCHIVED_SERVERS, Sample, Snapshot, next_payday
from .db import DB, Event, Preference, ALLOWED_THRESHOLDS

HR = "━━━━━━━━━━━━━━━━━━"
PERIOD_LABELS = {"1h": "1 час", "6h": "6 часов", "24h": "24 часа", "7d": "7 дней"}
PERIOD_TIMES = {"1h": timedelta(hours=1), "6h": timedelta(hours=6),
                "24h": timedelta(days=1), "7d": timedelta(days=7)}


def B(text: str, cb: str) -> Btn:
    return Btn(text=text, callback_data=cb)


def Mark(rows: list[list[Btn]]) -> Markup:
    return Markup(inline_keyboard=rows)


def flag(v: bool) -> str:
    return "✅" if v else "▫️"


def kb_home(pref: Preference | None = None) -> Markup:
    notify = "🔔 Уведомления · ВКЛ" if pref and pref.enabled else "🔔 Уведомления"
    return Mark([
        [B("🌵 Yuma", "s:25"), B("🌴 Vice City", "s:58")],
        [B("📊 Аналитика", "a:both:24h"), B("⚖️ Сравнение", "compare:30")],
        [B(notify, "settings"), B("🗂 События", "events")],
        [B("📅 Отчёт", "r:today"), B("ℹ️ О проекте", "help")],
        [B("↻ Обновить монитор", "home")],
    ])


def kb_back(refresh: str | None = None) -> Markup:
    rows = [[B("↻ Обновить", refresh)]] if refresh else []
    rows.append([B("⌂ Главный экран", "home")])
    return Mark(rows)


def kb_server_details(sid: int) -> Markup:
    return Mark([
        [B("📈 Динамика", f"a:{sid}:24h"), B("🔔 Сигналы", f"nt:{sid}")],
        [B("↻ Обновить", f"s:{sid}"), B("⌂ Главный экран", "home")],
    ])


def kb_analytics(selected: str = "both", period: str = "24h") -> Markup:
    def tab(caption: str, code: str) -> Btn:
        return B(("● " if selected == code else "") + caption, f"a:{code}:{period}")
    def period_tab(key: str) -> Btn:
        return B(("● " if period == key else "") + key, f"a:{selected}:{key}")
    return Mark([
        [tab("🌵 Yuma", "25"), tab("🌴 Vice", "58"), tab("⚖️ Оба", "both")],
        [period_tab(x) for x in PERIOD_LABELS],
        [B("🖼 Построить PNG-график", f"g:{selected}:{period}")],
        [B("📅 Отчёт", "r:today"), B("⌂ Главный экран", "home")],
    ])


def kb_chart_photo(selected: str, period: str) -> Markup:
    return Mark([
        [B("📊 К аналитике", f"a:{selected}:{period}"), B("⌂ Монитор", "home")],
    ])


def kb_charts() -> Markup:
    # Old command/callback compatibility; new UX lives at a:both:24h.
    return kb_analytics()


def kb_compare(period: int = 30) -> Markup:
    return Mark([
        [B(("● " if period == x else "") + f"{x} мин", f"compare:{x}") for x in (10, 30, 60)],
        [B("📊 Аналитика", "a:both:24h"), B("🖼 PNG сравнение", "g:both:24h")],
        [B("⌂ Главный экран", "home")],
    ])


def kb_events(archive: bool = False) -> Markup:
    return Mark([
        [B("🗂 Последние события" if archive else "📁 Архив Show Low",
           "events" if archive else "archive")],
        [B("↻ Обновить", "archive" if archive else "events"), B("⌂ Главный экран", "home")],
    ])


def kb_settings(pref: Preference) -> Markup:
    return Mark([
        [B("🔕 Выключить все оповещения" if pref.enabled else "🔔 Включить уведомления", "toggle:enabled")],
        [B("🌵 Yuma", "nt:25"), B("🌴 Vice City", "nt:58")],
        [B("💰 Пейдей / 📅 Отчёт", "nt:global"), B("🎚 Пороги", "thresholds")],
        [B("⌂ Главный экран", "home")],
    ])


def kb_server_settings(pref: Preference, sid: int) -> Markup:
    status = pref.status_25 if sid == 25 else pref.status_58
    slots = pref.slots_25 if sid == 25 else pref.slots_58
    threshold = pref.threshold_25 if sid == 25 else pref.threshold_58
    return Mark([
        [B(f"{flag(status)} Сигналы о доступности", f"toggle:status_{sid}")],
        [B(f"{flag(slots)} Свободные слоты (ниже {threshold})", f"toggle:slots_{sid}")],
        [B("🎚 Изменить порог онлайна", f"limits:{sid}")],
        [B("⬅️ Уведомления", "settings"), B("⌂ Главная", "home")],
    ])


def kb_global_settings(pref: Preference) -> Markup:
    return Mark([
        [B(f"{flag(pref.payday)} Пейдей · за 10 минут", "toggle:payday")],
        [B(f"{flag(pref.daily)} Отчёт · 21:00 МСК", "toggle:daily")],
        [B("⬅️ Уведомления", "settings"), B("⌂ Главная", "home")],
    ])


def kb_thresholds(pref: Preference, sid: int | None = None) -> Markup:
    if sid is None:
        return Mark([
            [B(f"🌵 Yuma · {pref.threshold_25}", "limits:25")],
            [B(f"🌴 Vice City · {pref.threshold_58}", "limits:58")],
            [B("⬅️ Уведомления", "settings")],
        ])
    current = pref.threshold_25 if sid == 25 else pref.threshold_58
    return Mark([
        [B(("✅ " if current == t else "") + str(t), f"limit:{sid}:{t}")
         for t in ALLOWED_THRESHOLDS[sid]],
        [B("⬅️ К серверу", f"nt:{sid}"), B("🔔 Все уведомления", "settings")],
    ])


def kb_report(period: str = "today") -> Markup:
    return Mark([
        [B(("● " if period == x else "") + label, f"r:{x}")
         for x, label in (("today", "Сегодня"), ("24h", "24 ч"), ("7d", "7 дней"))],
        [B("🖼 PNG-отчёт", f"rp:{period}")],
        [B("⌂ Главный экран", "home")],
    ])


def when(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%d.%m.%Y %H:%M:%S МСК")


def age_text(sample: Sample, now: datetime) -> str:
    age = max(0, int((now - sample.source_at).total_seconds()))
    return f"{age} сек." if age < 60 else f"{age // 60} мин."


def progress(online: int, max_slots: int, length: int = 12) -> str:
    filled = round(min(1, max(0, online / max(1, max_slots))) * length)
    return "▰" * filled + "▱" * (length-filled)


def delta_text(db: DB, sample: Sample, minutes: int = 10) -> str:
    change = db.delta(sample.server_id, sample, minutes)
    if change is None:
        return f"⏳ {minutes} мин. · история накапливается"
    d = change[0]
    if d > 0:
        return f"↗ +{d} игроков за {minutes} мин."
    if d < 0:
        return f"↘ {d} игроков за {minutes} мин."
    return f"→ Без изменений за {minutes} мин."


def one_server(db: DB, sample: Sample, now: datetime) -> str:
    title = f"{SERVER_ICONS[sample.server_id]} <b>{escape(sample.name.upper())}</b>"
    if not sample.fresh(now):
        return f"{title}\n⚪ Данные устарели · {age_text(sample, now)}"
    if not sample.responding:
        return f"{title}\n🔴 Нет ответа по данным API\n🕒 {when(sample.source_at)}"
    pct = 100 * sample.online / max(sample.max_slots, 1)
    free = max(0, sample.max_slots - sample.online)
    return (f"{title}  <b>{sample.online:,}</b><i>/{sample.max_slots:,}</i>\n"
            f"<code>{progress(sample.online, sample.max_slots)}</code>  <b>{pct:.1f}%</b>\n"
            f"🟢 В сети · {free} свободных мест\n"
            f"{delta_text(db, sample)}").replace(",", " ")


def dashboard(db: DB, snapshot: Snapshot | None, api_error: str | None = None) -> str:
    now = datetime.now(UTC)
    parts = ["🌵 <b>CHECKRP  /  LIVE</b>",
             "<i>Arizona Role Play    ·    Yuma × Vice City</i>", HR]
    if snapshot:
        for sid in SERVERS:
            sample = snapshot.servers.get(sid)
            parts.append(one_server(db, sample, now) if sample else f"⚪ {SERVERS[sid]} · нет данных")
            parts.append("")
        parts += [HR,
                  f"🕒 Запрос: <b>{now.astimezone(TZ).strftime('%H:%M:%S')}</b> МСК",
                  f"📡 Источник: {when(snapshot.servers[25].source_at)}",
                  "🌐 g-rp.me · HTTP API"]
        if snapshot.source_problem:
            parts.append("⚠️ Возможная массовая ошибка API; события не подтверждаются")
        if api_error:
            parts.append("⚠️ <b>Режим последних данных:</b> свежий запрос не удался")
    else:
        parts.append("⚠️ Не удалось получить данные из API. Повтори проверку позже.")
    payday = next_payday(now)
    mins = max(0, int((payday - now).total_seconds() / 60) + 1)
    parts += [f"💰 Пейдей: <b>{payday.strftime('%H:%M')} МСК</b> · ~{mins} мин.",
              "<i>Нажми на сервер для подробностей или открой аналитику.</i>"]
    return "\n".join(parts)


def server_details(db: DB, snapshot: Snapshot | None, sid: int, api_error: str | None) -> str:
    if snapshot is None or sid not in snapshot.servers:
        return "⚠️ Пока нет данных о сервере."
    s = snapshot.servers[sid]
    now = datetime.now(UTC)
    start = now - timedelta(hours=24)
    stats = db.stats(sid, start, now)
    lines = [f"{SERVER_ICONS[sid]} <b>{SERVERS[sid].upper()}  /  SERVER CARD</b>", HR,
             one_server(db, s, now), "", 
             f"👥 Свободных слотов: <b>{max(0, s.max_slots-s.online)}</b>",
             f"📈 Пик за час: <b>{s.peak_hour}</b>",
             f"🏆 Пик за день: <b>{s.peak_day}</b>",
             HR, "📊 <b>За последние 24 часа по истории CheckRP</b>"]
    if stats['n']:
        avg = f"{stats['avg_online']:.0f}" if stats['avg_online'] is not None else '—'
        lines += [f"Средний онлайн: <b>{avg}</b>",
                  f"Пик: <b>{stats['peak'] if stats['peak'] is not None else '—'}</b>",
                  f"Наблюдений: <b>{stats['n']}</b>"]
    else:
        lines.append("⌛ История ещё накапливается")
    lines += ["", f"🕒 Источник: {when(s.source_at)}",
              "<i>Потеря ответа ≠ подтверждённый рестарт.</i>"]
    if api_error:
        lines.append("⚠️ Данные могут быть устаревшими")
    return "\n".join(lines)


def analytics_text(db: DB, snapshot: Snapshot | None, api_error: str | None,
                   selected: str = "both", period: str = "24h") -> str:
    if period not in PERIOD_TIMES or selected not in {"25", "58", "both"}:
        return "⚠️ Неизвестный период или сервер."
    now = datetime.now(UTC)
    start = now - PERIOD_TIMES[period]
    lines = ["📊 <b>CHECKRP  /  ANALYTICS</b>",
             f"<i>Интервал: {PERIOD_LABELS[period]} · время МСК</i>", HR]
    sids = SERVERS if selected == "both" else (int(selected),)
    for sid in sids:
        name = SERVERS[sid]
        s = db.stats(sid, start, now)
        lines.append(f"\n{SERVER_ICONS[sid]} <b>{name}</b>")
        if not s['n']:
            lines.append("⌛ Нет измерений за выбранный период")
            continue
        avg = f"{s['avg_online']:.0f}" if s['avg_online'] is not None else "—"
        peak = s['peak'] if s['peak'] is not None else "—"
        bottom = s['minimum'] if s['minimum'] is not None else "—"
        pct = 100 * int(s['answered'] or 0) / max(1, int(s['n']))
        lines += [f"👥 Средний <b>{avg}</b> · пик <b>{peak}</b> · минимум {bottom}",
                  f"🟢 Ответы: {pct:.1f}% выборки · {s['n']} точек",
                  f"⏳ Нет ответа: ~{round(s['downtime_seconds']/60)} мин. (оценка)"]
        if snapshot and sid in snapshot.servers:
            smp = snapshot.servers[sid]
            if smp.fresh(now) and smp.responding:
                lines.append(f"📍 Сейчас: <b>{smp.online} / {smp.max_slots}</b> ({100*smp.online/max(1,smp.max_slots):.1f}%)")
    lines += ["", HR, "🖼 Выбери период и нажми «Построить PNG-график».",
              "<i>Пропуски API не заменяются вымышленными значениями.</i>"]
    if api_error:
        lines.append("⚠️ Последнее обращение к API не удалось")
    return "\n".join(lines)


def comparison_text(db: DB, snapshot: Snapshot | None, api_error: str | None,
                    minutes: int = 30) -> str:
    if not snapshot:
        return "⚖️ <b>СРАВНЕНИЕ</b>\n⚠️ API ещё не ответил."
    now = datetime.now(UTC)
    lines = ["⚖️ <b>CHECKRP  /  HEAD TO HEAD</b>", f"<i>Динамика за {minutes} минут</i>", HR]
    entries = []
    for sid in SERVERS:
        s = snapshot.servers[sid]
        lines.append(f"\n{SERVER_ICONS[sid]} <b>{s.name}</b>")
        if not s.fresh(now) or not s.responding:
            lines.append("⚪ Актуальных данных нет")
            continue
        pct = 100 * s.online / max(s.max_slots, 1)
        entries.append((sid, pct))
        lines += [f"👥 <b>{s.online:,} / {s.max_slots:,}</b> · <b>{pct:.1f}%</b>".replace(",", " "),
                  f"{delta_text(db, s, minutes)}"]
    if len(entries) == 2:
        first, second = entries
        diff = abs(first[1]-second[1])
        if diff < 0.05:
            lines.append("\n🤝 Заполненность практически одинакова")
        else:
            fuller = first if first[1] > second[1] else second
            lines.append(f"\n🏁 По % заполненности лидирует <b>{SERVERS[fuller[0]]}</b> на {diff:.1f} п.п.")
    lines += ["", HR, "<i>Проценты нормированы на вместимость: 1000 vs 750.</i>",
              f"🕒 {when(snapshot.fetched_at)}"]
    if api_error:
        lines.append("⚠️ Последние доступные данные")
    return "\n".join(lines)


def event_text(event: Event) -> str:
    name = SERVERS.get(event.server_id, ARCHIVED_SERVERS.get(event.server_id, f"#{event.server_id}"))
    if event.kind == "down":
        return f"🔴 <b>{escape(name)} · пропал ответ</b>\n🕒 {when(event.at)}\n⚠️ Причина неизвестна — возможна недоступность или рестарт."
    duration = (f"⏱️ Длительность: ~{max(1, round(event.duration_seconds/60))} мин.\n"
                if event.duration_seconds is not None else "")
    return f"🟢 <b>{escape(name)} · ответ восстановлен</b>\n🕒 {when(event.at)}\n{duration}<i>Причина неизвестна.</i>"


def events_text(events: list[Event], archive: bool = False) -> str:
    title = "📁 <b>SHOW LOW  /  АРХИВ</b>" if archive else "🗂 <b>CHECKRP  /  СОБЫТИЯ</b>"
    if not events:
        return title + "\n\n⌛ Пока событий нет."
    lines = [title, "<i>По переходам поля responding в HTTP API</i>", HR]
    for e in events:
        name = SERVERS.get(e.server_id, ARCHIVED_SERVERS.get(e.server_id, "Неизвестный"))
        action = "ответ восстановлен" if e.kind == "up" else "нет ответа"
        duration = f" · ~{max(1, round(e.duration_seconds / 60))} мин." if e.duration_seconds is not None else ""
        lines.append(f"{'🟢' if e.kind == 'up' else '🔴'} <b>{escape(name)}</b> · {action}\n{when(e.at)}{duration}")
    lines.append("<i>Это не доказательство фактического рестарта игры.</i>")
    return "\n\n".join(lines)


def settings_text(pref: Preference) -> str:
    lines = ["🔔 <b>CHECKRP  /  УВЕДОМЛЕНИЯ</b>", HR,
             f"Главная подписка: <b>{'ВКЛЮЧЕНА 🟢' if pref.enabled else 'ВЫКЛЮЧЕНА 🔴'}</b>", "",
             f"🌵 <b>Yuma</b> · статус {flag(pref.status_25)} · слоты {flag(pref.slots_25)}",
             f"    Порог: ниже <b>{pref.threshold_25}</b> из 1000",
             f"🌴 <b>Vice City</b> · статус {flag(pref.status_58)} · слоты {flag(pref.slots_58)}",
             f"    Порог: ниже <b>{pref.threshold_58}</b> из 750", "",
             f"💰 Пейдей (−10 мин): {flag(pref.payday)}",
             f"📅 Отчёт (21:00 МСК): {flag(pref.daily)}", "", HR,
             "<i>Выбери сервер или расписание для тонкой настройки.</i>"]
    if not pref.enabled:
        lines.append("🔕 Пока общая подписка выключена, оповещения не отправляются.")
    return "\n".join(lines)


def server_settings_text(pref: Preference, sid: int) -> str:
    status = pref.status_25 if sid == 25 else pref.status_58
    slots = pref.slots_25 if sid == 25 else pref.slots_58
    threshold = pref.threshold_25 if sid == 25 else pref.threshold_58
    return (f"{SERVER_ICONS[sid]} <b>{SERVERS[sid].upper()}  /  ALERT CENTER</b>\n{HR}\n"
            f"{'🟢 Подписка активна' if pref.enabled else '🔴 Общая подписка выключена'}\n\n"
            f"{flag(status)} Потеря / восстановление ответа\n"
            f"{flag(slots)} Свободные слоты при онлайне ниже <b>{threshold}</b>\n\n"
            "<i>При первом включении свободных слотов бот может отправить одно уведомление, если онлайн уже ниже порога. "
            "Повторы при неизменном онлайне не отправляются.</i>")


def schedule_settings_text(pref: Preference) -> str:
    return (f"💰 <b>CHECKRP  /  РАСПИСАНИЕ</b>\n{HR}\n"
            f"{flag(pref.payday)} <b>Пейдей</b>\nЗа 10 минут до :00 и :30 по МСК\n\n"
            f"{flag(pref.daily)} <b>Ежедневный отчёт</b>\nОколо 21:00 МСК, по накопленным данным\n\n"
            f"{'🟢 Общая подписка включена' if pref.enabled else '🔴 Общая подписка выключена'}\n"
            "<i>Оповещения о пейдеях идут по расписанию, не из игрового API.</i>")


def thresholds_text(pref: Preference, sid: int | None = None) -> str:
    if sid is None:
        return (f"🎚 <b>ПОРОГИ СВОБОДНЫХ СЛОТОВ</b>\n{HR}\n"
                f"🌵 Yuma: <b>{pref.threshold_25}</b> из 1000\n"
                f"🌴 Vice City: <b>{pref.threshold_58}</b> из 750\n\n"
                "Нажми сервер для выбора порога.")
    current = pref.threshold_25 if sid == 25 else pref.threshold_58
    return (f"🎚 <b>{SERVERS[sid].upper()} · ПОРОГ</b>\n{HR}\n"
            f"Текущее значение: <b>{current}</b>\n"
            "Уведомление придёт при пересечении порога вниз (и один раз после активации, если онлайн уже ниже).\n\n"
            "Выбери значение кнопкой ниже.")


def stats_text(db: DB, since: datetime, until: datetime, label: str) -> str:
    lines = [f"📅 <b>CHECKRP  /  {escape(label)}</b>", HR]
    for sid in SERVERS:
        s = db.stats(sid, since, until)
        lines.append(f"\n{SERVER_ICONS[sid]} <b>{SERVERS[sid]}</b>")
        if not s['n']:
            lines.append("⌛ История ещё накапливается")
            continue
        avg = f"{s['avg_online']:.0f}" if s['avg_online'] is not None else "—"
        pct = 100 * int(s['answered'] or 0) / max(1, int(s['n']))
        lines += [f"👥 Средний <b>{avg}</b> · пик <b>{s['peak'] if s['peak'] is not None else '—'}</b>",
                  f"📉 Минимум {s['minimum'] if s['minimum'] is not None else '—'}",
                  f"🟢 Ответ: {pct:.1f}% точек · наблюдений: {s['n']}",
                  f"🔴 Событий потери ответа: {s['downs']}",
                  f"⏳ Недоступность: ~{round(s['downtime_seconds']/60)} мин. (оценка)"]
    lines += ["", HR, f"🕒 {when(until)}", "<i>Основано на истории HTTP API; не показатель истинного аптайма игры.</i>"]
    return "\n".join(lines)


HELP = ("ℹ️ <b>CHECKRP 4.5 · НАВИГАЦИЯ</b>\n\n"
        "⌂ /check — монитор Yuma / Vice City\n"
        "📊 /stats — единая аналитика + PNG\n"
        "⚖️ /compare — сравнение серверов\n"
        "🔔 /notify — центр уведомлений\n"
        "🗂 /events — события и архив Show Low\n"
        "📅 /report — отчёты за разные периоды\n"
        "🔎 /player — ограничение API по игрокам\n"
        "🛠 /health — только администратор\n\n"
        "🌐 https://online.g-rp.me/api/v1/projects/arizona-rp\n"
        "🕒 Все часы по МСК. API может запаздывать.\n"
        "<i>Это обычные Telegram-сообщения с inline-кнопками, не Web App.</i>")

PLAYER_NOTICE = ("🔎 <b>ПОИСК ИГРОКА</b>\n\n"
                 "API g-rp.me не содержит никнеймы и игровые ID. "
                 "Проверку отдельного игрока нельзя достоверно выполнить без другого разрешённого источника.")
