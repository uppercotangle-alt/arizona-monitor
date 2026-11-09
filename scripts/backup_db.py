#!/usr/bin/env python3
"""Online-safe SQLite backups using backup API; keep a week of daily snapshots."""
from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
source = Path(os.getenv('DB_PATH', str(ROOT / 'data/checkrp.sqlite3'))).expanduser()
target_dir = ROOT / 'backups'
target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
if not source.is_file():
    raise SystemExit(f'No source database found: {source}')
name = 'checkrp-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '.sqlite3'
target = target_dir / name
with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as original:
    with sqlite3.connect(target) as snapshot:
        original.backup(snapshot)
target.chmod(0o600)
for old in sorted(target_dir.glob('checkrp-*.sqlite3'), reverse=True)[7:]:
    old.unlink()
print(f'Backup created: {target}')
