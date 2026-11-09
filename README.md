# 🌵 CheckRP 4.5 — Telegram-native redesign

**Yuma (25) × Vice City (58)** on Oracle Cloud / Ubuntu 24.04. Uses **only** the
[g-rp.me HTTP API](https://online.g-rp.me/api/v1/projects/arizona-rp),
Python 3.11+, aiogram 3, SQLite, Matplotlib. **No Web App, no UDP.**

## What's new in 4.5

- **New monitor message:** separate server cards with online, occupancy, free slots, delta, freshness, time to payday; existing message edited on refresh.
- **Analytics:** a *single message* with selectable **Yuma / Vice City / both** and **1h / 6h / 24h / 7d** periods. The `🖼 Построить PNG-график` button sends a proper image (no dummy data).
- **Server details:** 24h stats and peaks, with direct links to graphs and notifications.
- **Notification center:** global enable/disable, separate pages for each server, separate schedule page, individual threshold page. User subscriptions, thresholds and SQLite history retained.
- **Reports:** today, last 24h or last 7d; an optional PNG button.
- **Charts:** redesigned dark 16:9 visual cards, new metric headers, Moscow-time labels, separate Yuma / Vice City accent colors, no zeros manufactured from missing API data.
- **Compatibility:** old `charts` and `report` callback actions still route to new pages. `Show Low` remains an events archive, never incorrectly converted to Vice City history.
- **Testing:** core, API sample processing, SQLite migration, Telegram callback contracts, PNG generation.

## Notes and limitations

- `/check`, `/stats`, `/compare`, `/notify`, `/events`, `/report`, `/health` supported. `/player` is informational: the public API provides no player nicknames/IDs.
- A disappearance of HTTP `responding` **is not proof** of a game restart.
- No public API is guaranteed to update every 20 seconds; sampling/alerts handle stale timestamps.
- A report reflects the data *accumulated by your own bot*; it is not a complete external historical feed.
- All schedules use **Moscow time (Europe/Moscow)**, including 21:00 daily reports and payday at `:00` / `:30`.
- `active (running)` is not enough to prove a successful deployment. Check the logs and interact with Telegram.

## Update your existing Oracle VPS — after uploading the ZIP to GitHub

Do **not** upload `.env`, `data/`, SQLite databases, or SSH private keys to GitHub.
Upload all files **at the root of** `uppercotangle-alt/arizona-monitor` (no nested ZIP directory).

On Windows PowerShell, connect using your existing key:

```powershell
& "$env:WINDIR\System32\OpenSSH\ssh.exe" -i "$env:USERPROFILE\.ssh\checkrp_oracle" ubuntu@152.70.183.27
```

Inside **Ubuntu**:

```bash
cd ~/checkrp
# First review what GitHub will change:
git fetch origin main
git diff --stat HEAD origin/main

# Then run the controlled update with a database backup and code rollback:
bash scripts/deploy_v45.sh
```

If `scripts/deploy_v45.sh` isn't on the *old* deployed branch yet, run:

```bash
cd ~/checkrp
curl -fsSL https://raw.githubusercontent.com/uppercotangle-alt/arizona-monitor/main/scripts/deploy_v45.sh -o /tmp/checkrp-deploy-v45.sh
bash /tmp/checkrp-deploy-v45.sh
```

Only run that second option **after** inspecting the script in your own GitHub repo; remote content may change. You may instead copy the script from this ZIP to the VPS with `scp` for maximum control.

When successful:

```bash
sudo systemctl status checkrp --no-pager -l
sudo journalctl -u checkrp -n 60 --no-pager -l
systemctl is-enabled checkrp
```

Then in Telegram: `/check` → `📊 Аналитика` → change tabs and period → `🖼 Построить PNG-график`. `/notify` → configure Yuma/Vice City independently. `/report` → choose time range → generate PNG. Old v4 messages still have working `charts` / `report` buttons where possible.

### Manual rollback

The updater automatically restores old tracked code if installation/tests fail; the SQLite backup is under `~/checkrp/backups/pre-v45-<timestamp>.sqlite3`.
To revert later, after recording the SHA of v4 before upgrading:

```bash
cd ~/checkrp
sudo systemctl stop checkrp
git reset --hard OLD_V4_SHA  # do NOT paste literally; substitute the actual saved SHA
sudo systemctl start checkrp
```

Because v4.5 does not change DB schema, data needn't be rolled back for ordinary code rollback. Never replace current SQLite with an old backup unless deliberately discarding newer data.

### Local tests

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

The current repository was tested with synthetic data locally. Production verification requires an installed aiogram and a live Telegram bot on your Oracle VPS.

## Security

Keep `BOT_TOKEN` exclusively in `.env` or a secret store, not in GitHub. Rotate any token previously leaked in chats or screenshots. `.env`, `data`, SQLite, `.venv`, backups are gitignored.
