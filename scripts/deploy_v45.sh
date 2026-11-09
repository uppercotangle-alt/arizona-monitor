#!/usr/bin/env bash
# Update an EXISTING Oracle CheckRP installation after pushing v4.5 to GitHub.
# Keeps .env / SQLite / .venv. Backs up SQLite safely and rolls back code on error.
set -Eeuo pipefail
umask 077
APP="/home/ubuntu/checkrp"
cd "$APP"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "STOP: uncommitted tracked changes found. Commit/stash them first." >&2
  exit 2
fi
if [[ ! -f .env ]]; then
  echo "STOP: .env not found in $APP" >&2
  exit 2
fi
OLD_SHA="$(git rev-parse HEAD)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p backups
BACKUP="backups/pre-v45-${STAMP}.sqlite3"
CHANGED=0
rollback() {
  rc=$?
  trap - ERR INT TERM
  echo "ERROR: update failed (code $rc). Rolling back tracked code..." >&2
  if [[ $CHANGED == 1 ]]; then
    git reset --hard "$OLD_SHA" >&2 || true
  fi
  sudo systemctl start checkrp || true
  exit "$rc"
}
trap rollback ERR INT TERM

echo "Fetching latest code (the running bot is not stopped yet)..."
git fetch origin main
if [[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]]; then
  echo "Already on origin/main. No update required."
  exit 0
fi
# Do not rewrite unrelated / diverged main histories.
git merge-base --is-ancestor HEAD origin/main

echo "Stopping CheckRP for consistent database snapshot..."
sudo systemctl stop checkrp
python3 - "$BACKUP" <<'PY'
from pathlib import Path
from sqlite3 import connect
from sys import argv
settings = Path('.env').read_text(encoding='utf-8').splitlines()
file = 'data/checkrp.sqlite3'
for item in settings:
    if item.startswith('DB_PATH='):
        file = item.split('=',1)[1].strip().strip('"\'')
        break
source = Path(file).expanduser()
if not source.is_absolute():
    source = Path.cwd()/source
if source.exists():
    with connect(f'file:{source}?mode=ro', uri=True) as src, connect(argv[1]) as dst:
        src.backup(dst)
    print(f'SQLite backup: {argv[1]}')
else:
    print('No existing database yet; first-run mode.')
PY

echo "Upgrading tracked files..."
git merge --ff-only origin/main
CHANGED=1
if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
sudo systemctl start checkrp
sleep 3
sudo systemctl is-active --quiet checkrp
trap - ERR INT TERM
echo "SUCCESS: CheckRP updated. SQLite backup: $BACKUP"
echo "Logs: sudo journalctl -u checkrp -n 60 --no-pager -l"
