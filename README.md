# 🌵 CheckRP v3 — Yuma / Show Low (Arizona RP)

Telegram bot written in **Python 3.12 + aiogram 3**, using **only** the public HTTP API at https://online.g-rp.me/api/v1/projects/arizona-rp. **No UDP, no SA-MP protocol, no site passwords/cookies.**

## Features

- `/check`, `/start`: attractive dashboard, status, online and 10-minute delta, source freshness and peaks.
- Server details (Yuma ID `25`, Show Low ID `52`), `/events` history.
- `/stats`: PNG history charts (1h / 24h / 7d), average and peak. Charts accumulate from the first run of v3; past history is not invented.
- Two separate confirmed *new API updates* needed to announce a loss/return of `responding`. Stale and failed API responses do not count. Major feed outages suppress server restart notifications.
- `/notify`: per-server toggles for outages/restorations and **online below 980**; no repeated alerts until the online recovers to >=980 and crosses below again.
- Opt-in **payday reminders** at `XX:20` and `XX:50` Moscow time (10 min before `XX:30` / `XX:00`). This uses the *user-provided payday schedule*, not an Arizona RP live event feed.
- Opt-in **daily reports** around 21:00 Moscow time, plus on-demand `/report`.
- SQLite (`data/checkrp.sqlite3`), cooldown on buttons, subscription preferences, time-zone `Europe/Moscow` (UTC+3).
- `/player` explains why searching the online status / SA-MP session ID by nickname is not yet possible from this aggregate API. It **does not invent player status**.

## Quick setup on EthioDeploy

1. Create a fresh GitHub repo and commit **every file and the `checkrp/` folder**, but **never `.env` or `.sqlite3`**.
2. EthioDeploy: choose the repository. Select **Auto (Dockerfile if present)**. Leave build/start commands blank; the included Dockerfile controls installation and execution.
3. Add Environment Variables as separate key/value entries:

   | Key | Value |
   |---|---|
   | `BOT_TOKEN` | **New** Telegram token from BotFather |
   | `ADMIN_ID` | Your Telegram numeric ID, optional |
   | `POLL_SECONDS` | `20` |
   | `DB_PATH` | `./data/checkrp.sqlite3` |

   `Runtime only` and `Sensitive` are recommended for `BOT_TOKEN`.
4. Deploy, open your bot, `/check`, `/notify`. Enable payday and daily reports separately.

**Persistence warning:** a free host may delete container-local SQLite between deploys / restarts. Then subscriptions, graph data, event history and delivery deduplication are lost. To persist permanently, mount a persistent volume at `/app/data` (if offered), or migrate the DB to managed PostgreSQL. This version intentionally uses SQLite and does **not** claim cross-deploy durability on ephemeral hosting. Only run *one* active bot instance with the same Telegram token (polling conflict otherwise).

## API and timing

Source server data use `updated_at`. Freshness threshold: 180 sec; actual real-world event delays depend on the source's schedule. `POLL_SECONDS=20` means *HTTP fetch interval*, not instantaneous SA-MP observations. The 10-minute delta uses samples approximately 10 to 15 minutes apart; it shows `—` until enough data exists. UI shows Moscow time regardless of phone locale or hosting time zone.

The API only reports `responding`; it's not an authoritative restart flag. Notification texts therefore say **possible restart**. If the upstream sends false status or fails to observe a restart between updates, a bot cannot be certain or necessarily detect it.

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and set BOT_TOKEN
python bot.py
```

## Tests

```bash
python -m unittest discover -s tests -v
```

No Telegram token is needed for unit tests. This is a tested starting point, not a guarantee of bug-free behavior under all Telegram, hosting and API conditions.
