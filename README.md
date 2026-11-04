# Arizona RP Monitor — direct SA-MP Query

Checks `185.169.134.107:7777` (Yuma) and `80.66.82.190:7777` (Show Low) using SA-MP UDP Query (`samp-client`) instead of g-rp.me HTTP API.

## Deploy
- Replace existing `bot.py` and `requirements.txt` in your GitHub repo.
- EthioDeploy Install Command: `pip install -r requirements.txt`.
- Start Command: `python bot.py`.
- Environment: BOT_TOKEN (a newly revoked/rotated token), ADMIN_ID, POLL_SECONDS=5, QUERY_TIMEOUT=4, TZ=Europe/Kyiv.
- Redeploy and check `/check`.

## Important
- Direct SA-MP Query requires outbound UDP access. If the hosting provider blocks UDP or a game server disables queries, the bot may show no response despite active players.
- Notifications show possible restarts, not authoritative restart events. Two consecutive query failures are required after a prior known online state. Avoid excessively low query intervals.
- `samp-client` uses blocking sockets; the bot runs them in threads so Telegram polling stays responsive. Timeouts cannot instantly cancel underlying OS-level calls.
- SQLite subscriptions are stored in `monitor.db`; ephemeral hosting storage can reset this data on redeploy.
- NEVER upload .env / token to GitHub; revoke the token already posted publicly via @BotFather.
