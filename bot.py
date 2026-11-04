import asyncio
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import time
from samp_client.client import SampClient
import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SERVERS = {"Yuma": ("185.169.134.107", 7777), "Show Low": ("80.66.82.190", 7777)}
POLL_SECONDS = max(3, int(os.getenv("POLL_SECONDS", "5")))
DB_PATH = os.getenv("DB_PATH", "monitor.db")
TZ = ZoneInfo(os.getenv("TZ", os.getenv("TIMEZONE", "Europe/Kyiv")))
QUERY_TIMEOUT = float(os.getenv("QUERY_TIMEOUT", "4"))
API_URL = "https://online.g-rp.me/api/v1/projects/arizona-rp"
MAX_API_AGE = int(os.getenv("MAX_API_AGE_SECONDS", "180"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("arizona-monitor")

bot = Bot(TOKEN)
dp = Dispatcher()
monitor_task: asyncio.Task | None = None
# Candidate state must be observed twice before confirmed transition.
states = {name: {"online": None, "candidate": None, "candidate_count": 0,
                 "players": None, "slots": None, "updated_at": None, "last_restart": None, "stale": True, "age": None, "source": "none", "display_online": None}
          for name in ("Yuma", "Show Low")}
restart_pending = set()

def now_text():
    return datetime.now(TZ).strftime("%d.%m.%Y %H:%M:%S")

def kb():
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Проверить сейчас", callback_data="check")
    b.button(text="📡 Серверы", callback_data="servers")
    b.button(text="🔔 Подписаться", callback_data="sub")
    b.button(text="🔕 Отписаться", callback_data="unsub")
    b.adjust(1)
    return b.as_markup()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS subscribers(
            chat_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL)""")
        await db.commit()

async def subscribers():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT chat_id FROM subscribers")
        return [r[0] for r in await cur.fetchall()]

def _sync_query(host: str, port: int) -> tuple[int, int]:
    # samp-client is synchronous; called inside an executor to keep aiogram responsive.
    with SampClient(address=host, port=port) as client:
        info = client.get_server_info()
        return int(info.players), int(info.max_players)

async def query_server(name: str, host: str, port: int) -> dict:
    try:
        players, slots = await asyncio.wait_for(
            asyncio.to_thread(_sync_query, host, port), timeout=QUERY_TIMEOUT
        )
        return {"online": True, "players": players, "slots": slots,
                "updated_at": now_text(), "stale": False, "age": 0, "source": "udp", "display_online": True}
    except Exception as exc:
        log.warning("UDP query failed %s (%s:%s): %s: %s", name, host, port, type(exc).__name__, exc)
        # A timeout can also mean UDP filtering, not necessarily a server restart.
        return {"online": False, "players": None, "slots": None,
                "updated_at": None, "stale": True, "age": None, "source": "none", "display_online": None}

async def api_fallback(failed_names):
    """HTTP backup for display only; NEVER used to confirm UDP restarts."""
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(API_URL, params={"_ts": str(time.time_ns())},
                                   headers={"Cache-Control": "no-cache"}) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
        by_name = {str(v.get("name", "")).casefold(): v for v in payload.get("servers", [])}
        result = {}
        for name in failed_names:
            entry = by_name.get(name.casefold())
            if not entry:
                continue
            raw_date = entry.get("updated_at")
            age = None
            try:
                dt = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
            except (TypeError, ValueError):
                pass
            result[name] = {"online": None,  # no transition evidence from HTTP
                            "display_online": entry.get("responding"),
                            "players": entry.get("online"),
                            "slots": entry.get("max_slots"),
                            "updated_at": raw_date,
                            "stale": age is None or age > MAX_API_AGE,
                            "age": age, "source": "api"}
        return result
    except Exception as exc:
        log.warning("HTTP fallback failed: %s: %s", type(exc).__name__, exc)
        return {}

async def samp_snapshot():
    results = await asyncio.gather(*(
        query_server(name, host, port) for name, (host, port) in SERVERS.items()
    ))
    data = dict(zip(SERVERS, results))
    failed = [name for name, item in data.items() if item["source"] == "none"]
    if failed:
        data.update(await api_fallback(failed))
    return data

def render(snapshot=None):
    lines = ["🌵 <b>ARIZONA RP MONITOR</b>", "━━━━━━━━━━━━━━━━━━"]
    for name in SERVERS:
        s = states[name]
        source = s.get("source", "none")
        if source == "udp":
            status = "🟢 <b>ONLINE (UDP)</b>"
            source_line = "⚡ Прямой ответ сервера"
        elif source == "api":
            status = "🟡 <b>ПО ДАННЫМ API</b>" if not s["stale"] else "⚠️ <b>УСТАРЕВШИЕ ДАННЫЕ API</b>"
            source_line = "📡 UDP недоступен, показан резервный API"
        else:
            status = "🔴 <b>НЕТ ДАННЫХ</b>"
            source_line = "⚠️ UDP не ответил, резервный API тоже недоступен"
        players = f'{s["players"]}/{s["slots"]}' if s["players"] is not None and s["slots"] is not None else "—"
        lines += [f"\n<b>{name}</b>", f"Статус: {status}", f"Игроки: <b>{players}</b>", source_line]
        if source == "api":
            lines.append(f'🕒 Обновление источника: {s["updated_at"] or "неизвестно"}; возраст: {s["age"] if s["age"] is not None else "?"} сек.')
    lines += ["", "━━━━━━━━━━━━━━━━━━", f"🕒 Проверено: {now_text()}",
              "<i>UDP без ответа не означает, что сервер выключен.</i>"]
    return "\n".join(lines)

async def notify(text):
    for chat_id in await subscribers():
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception as e:
            log.warning("Notification to %s failed: %s", chat_id, e)

async def process_snapshot(data, announce=True):
    for name, incoming in data.items():
        st = states[name]
        observed = incoming["online"]
        st["players"] = incoming["players"]
        st["slots"] = incoming["slots"]
        st["updated_at"] = incoming["updated_at"]
        st["stale"] = incoming["stale"]
        st["age"] = incoming["age"]
        st["source"] = incoming.get("source", "none")
        st["display_online"] = incoming.get("display_online")
        if incoming["stale"] or incoming.get("source") != "udp":
            continue  # HTTP is display-only, UDP failures do NOT prove restart.
        if observed is None:
            continue
        # First observation initializes state silently.
        if st["online"] is None:
            st["online"] = observed
            st["candidate"] = None
            st["candidate_count"] = 0
            continue
        if observed == st["online"]:
            st["candidate"] = None
            st["candidate_count"] = 0
            # If a server went down and is now responding, announce restart completion.
            if name in restart_pending and observed:
                restart_pending.discard(name)
                st["last_restart"] = now_text()
                if announce:
                    await notify(f"🟢 <b>{name}</b>: сервер снова отвечает. Возможный рестарт завершён.\nОнлайн: {st['players']}/{st['slots']}")
            continue
        if st["candidate"] != observed:
            st["candidate"] = observed
            st["candidate_count"] = 1
        else:
            st["candidate_count"] += 1
        if st["candidate_count"] >= 2:
            previous = st["online"]
            st["online"] = observed
            st["candidate"] = None
            st["candidate_count"] = 0
            if previous and not observed:
                restart_pending.add(name)
                if announce:
                    await notify(f"🔴 <b>{name}</b>: дважды не ответил на SA-MP UDP Query.\nВозможен рестарт, сбой или фильтрация запросов.")
            elif not previous and observed:
                restart_pending.discard(name)
                if announce:
                    await notify(f"🟢 <b>{name}</b>: сервер снова отвечает!\nОнлайн: {st['players']}/{st['slots']}")

async def refresh():
    data = await samp_snapshot()
    await process_snapshot(data)
    return data

async def monitor_loop():
    while True:
        try:
            await refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("SA-MP monitoring error")
        await asyncio.sleep(POLL_SECONDS)

@dp.message(Command("start"))
async def start(m: Message):
    await m.answer("🌵 <b>Arizona RP Monitor</b>\n\nПрямой SA-MP Query для Yuma и Show Low с резервным API.\nНажми «Проверить сейчас» или включи уведомления.", parse_mode="HTML", reply_markup=kb())

@dp.message(Command("check"))
async def check_cmd(m: Message):
    msg = await m.answer("⏳ Проверяю SA-MP серверы напрямую…")
    try:
        await refresh()
        await msg.edit_text(render(), parse_mode="HTML", reply_markup=kb())
    except Exception:
        log.exception("Manual check failed")
        await msg.edit_text("⚠️ Ошибка проверки серверов. Попробуй позже.", reply_markup=kb())

@dp.callback_query(F.data == "check")
async def check_btn(c: CallbackQuery):
    await c.answer("Проверяю серверы…")
    try:
        await refresh()
        await c.message.edit_text(render(), parse_mode="HTML", reply_markup=kb())
    except Exception:
        await c.message.answer("⚠️ Ошибка проверки серверов.")

@dp.callback_query(F.data == "servers")
async def servers_btn(c: CallbackQuery):
    await c.answer("Обновляю данные…")
    try:
        await refresh()
        await c.message.answer(render(), parse_mode="HTML", reply_markup=kb())
    except Exception:
        log.exception("Servers button refresh failed")
        await c.message.answer("⚠️ Ошибка проверки серверов.")

@dp.callback_query(F.data == "sub")
async def sub(c: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO subscribers(chat_id,created_at) VALUES(?,?)",
                         (c.message.chat.id, now_text()))
        await db.commit()
    await c.answer("Уведомления включены!", show_alert=True)

@dp.callback_query(F.data == "unsub")
async def unsub(c: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM subscribers WHERE chat_id=?", (c.message.chat.id,))
        await db.commit()
    await c.answer("Уведомления отключены.", show_alert=True)

@dp.message(Command("admin"))
async def admin(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    count = len(await subscribers())
    await m.answer(f"🛠 <b>Админ-панель</b>\nПодписчиков: {count}\nИнтервал: {POLL_SECONDS} сек.\nИсточник: SA-MP UDP + резервный HTTP API", parse_mode="HTML")

@dp.message(Command("ping"))
async def ping(m: Message):
    await m.answer("pong 🏓")

async def main():
    global monitor_task
    if not TOKEN:
        raise RuntimeError("Укажи BOT_TOKEN в .env")
    await init_db()
    try:
        # Initial SA-MP query sets baseline without announcements.
        try:
            data = await samp_snapshot()
            for name, item in data.items():
                states[name].update(online=item["online"] if item["online"] else None, players=item["players"],
                                   slots=item["slots"], updated_at=item["updated_at"],
                                   stale=item["stale"], age=item["age"],
                                   source=item.get("source", "none"),
                                   display_online=item.get("display_online"))
        except Exception:
            log.exception("Initial SA-MP query failed; monitor will retry")
        monitor_task = asyncio.create_task(monitor_loop())
        await dp.start_polling(bot)
    finally:
        if monitor_task:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
