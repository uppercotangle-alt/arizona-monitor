import asyncio
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
API_URL = "https://online.g-rp.me/api/v1/projects/arizona-rp"
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "20"))
DB_PATH = os.getenv("DB_PATH", "monitor.db")
TZ = ZoneInfo(os.getenv("TZ", os.getenv("TIMEZONE", "Europe/Kyiv")))
MAX_DATA_AGE_SECONDS = int(os.getenv("MAX_DATA_AGE_SECONDS", "180"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("arizona-monitor")

bot = Bot(TOKEN)
dp = Dispatcher()
http: aiohttp.ClientSession | None = None
monitor_task: asyncio.Task | None = None
# Candidate state must be observed twice before confirmed transition.
states = {name: {"online": None, "candidate": None, "candidate_count": 0,
                 "players": None, "slots": None, "updated_at": None, "last_restart": None, "stale": True, "age": None}
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

async def api_snapshot():
    assert http is not None
    async with http.get(
        API_URL,
        params={"_ts": str(int(datetime.now(timezone.utc).timestamp()))},
        headers={"Cache-Control": "no-cache, no-store", "Pragma": "no-cache"},
    ) as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)
    found = {}
    for server in data.get("servers", []):
        name = server.get("name")
        if name in states:
            found[name] = server
    result = {}
    for name in states:
        s = found.get(name)
        if not s:
            result[name] = {"online": None, "players": None, "slots": None, "updated_at": None, "stale": True, "age": None}
        else:
            raw_updated = s.get("updated_at")
            age = None
            if raw_updated:
                try:
                    updated = datetime.fromisoformat(raw_updated.replace("Z", "+00:00"))
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=timezone.utc)
                    age = max(0, int((datetime.now(timezone.utc) - updated).total_seconds()))
                except (ValueError, TypeError):
                    log.warning("Invalid updated_at for %s: %r", name, raw_updated)
            stale = age is None or age > MAX_DATA_AGE_SECONDS
            result[name] = {
                "online": bool(s.get("responding", False)),
                "players": s.get("online"),
                "slots": s.get("max_slots"),
                "updated_at": raw_updated,
                "stale": stale,
                "age": age,
            }
    return result

def render(snapshot=None):
    lines = ["🌵 <b>ARIZONA RP MONITOR</b>", "━━━━━━━━━━━━━━━━━━"]
    for name in ("Yuma", "Show Low"):
        s = states[name]
        if s["stale"]:
            status = "⚠️ <b>ДАННЫЕ УСТАРЕЛИ</b>"
        elif s["online"] is True:
            status = "🟢 <b>ONLINE</b>"
        elif s["online"] is False:
            status = "🔴 <b>OFFLINE</b>"
        else:
            status = "🟡 <b>ОЖИДАНИЕ ДАННЫХ</b>"
        players = f'{s["players"]}/{s["slots"]}' if s["players"] is not None and s["slots"] is not None else "—"
        lines += [f"\n<b>{name}</b>", f"Статус: {status}", f"Онлайн: <b>{players}</b>"]
        if s["updated_at"]:
            lines.append(f'<i>API обновлено: {s["updated_at"]}</i>')
        if s["stale"]:
            age_text = f'{s["age"]} сек.' if s["age"] is not None else "неизвестно"
            lines.append(f'⚠️ Это последние известные значения (возраст: {age_text}), не онлайн в реальном времени.')
    lines += ["", "━━━━━━━━━━━━━━━━━━", f"🕒 Проверено: {now_text()}"]
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
        if incoming["stale"]:
            log.warning("Stale API result for %s: updated_at=%s age=%s", name, st["updated_at"], st["age"])
            continue  # Never generate restart notifications from stale data.
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
                    await notify(f"🔴 <b>{name}</b>: сервер перестал отвечать.\nВозможен рестарт или сбой.")
            elif not previous and observed:
                restart_pending.discard(name)
                if announce:
                    await notify(f"🟢 <b>{name}</b>: сервер снова отвечает!\nОнлайн: {st['players']}/{st['slots']}")

async def refresh():
    data = await api_snapshot()
    await process_snapshot(data)
    return data

async def monitor_loop():
    while True:
        try:
            await refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("API monitoring error")
        await asyncio.sleep(POLL_SECONDS)

@dp.message(Command("start"))
async def start(m: Message):
    await m.answer("🌵 <b>Arizona RP Monitor</b>\n\nМониторинг Yuma и Show Low через g-rp.me.\nНажми «Проверить сейчас» или включи уведомления.", parse_mode="HTML", reply_markup=kb())

@dp.message(Command("check"))
async def check_cmd(m: Message):
    msg = await m.answer("⏳ Получаю данные g-rp.me…")
    try:
        await refresh()
        await msg.edit_text(render(), parse_mode="HTML", reply_markup=kb())
    except Exception:
        log.exception("Manual check failed")
        await msg.edit_text("⚠️ API временно недоступно. Попробуй позже.", reply_markup=kb())

@dp.callback_query(F.data == "check")
async def check_btn(c: CallbackQuery):
    await c.answer("Запрашиваю API…")
    try:
        await refresh()
        await c.message.edit_text(render(), parse_mode="HTML", reply_markup=kb())
    except Exception:
        await c.message.answer("⚠️ API временно недоступно.")

@dp.callback_query(F.data == "servers")
async def servers_btn(c: CallbackQuery):
    await c.answer("Обновляю данные…")
    try:
        await refresh()
        await c.message.answer(render(), parse_mode="HTML", reply_markup=kb())
    except Exception:
        log.exception("Servers button refresh failed")
        await c.message.answer("⚠️ API временно недоступно.")

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
    await m.answer(f"🛠 <b>Админ-панель</b>\nПодписчиков: {count}\nИнтервал: {POLL_SECONDS} сек.\nAPI: g-rp.me", parse_mode="HTML")

@dp.message(Command("ping"))
async def ping(m: Message):
    await m.answer("pong 🏓")

async def main():
    global http, monitor_task
    if not TOKEN:
        raise RuntimeError("Укажи BOT_TOKEN в .env")
    await init_db()
    timeout = aiohttp.ClientTimeout(total=12)
    http = aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "ArizonaMonitorBot/1.0"})
    try:
        # Initial API read sets baseline without announcements.
        try:
            data = await api_snapshot()
            for name, item in data.items():
                states[name].update(online=item["online"], players=item["players"],
                                   slots=item["slots"], updated_at=item["updated_at"],
                                   stale=item["stale"], age=item["age"])
        except Exception:
            log.exception("Initial API request failed; monitor will retry")
        monitor_task = asyncio.create_task(monitor_loop())
        await dp.start_polling(bot)
    finally:
        if monitor_task:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
        await http.close()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
