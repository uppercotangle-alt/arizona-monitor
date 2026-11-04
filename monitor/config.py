from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

API_URL = "https://online.g-rp.me/api/v1/projects/arizona-rp"
SERVERS = {25: "Yuma", 52: "Show Low"}


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        result = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


@dataclass(frozen=True, slots=True)
class Config:
    token: str
    admin_id: int
    poll_seconds: int
    max_data_age_seconds: int
    tz: ZoneInfo
    db_path: Path

    @classmethod
    def from_env(cls) -> Config:
        load_dotenv(override=False)
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token or token == "PASTE_NEW_TOKEN_HERE":
            raise ValueError("Set BOT_TOKEN in environment variables (not in GitHub)")
        admin_id = _int_env("ADMIN_ID", 0, 0, 10**16)
        poll_seconds = _int_env("POLL_SECONDS", 20, 10, 3600)
        max_age = _int_env("MAX_DATA_AGE_SECONDS", 180, 30, 3600)
        tz_name = os.getenv("TZ", "Europe/Kyiv").strip()
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown TZ: {tz_name}") from exc
        db_path = Path(os.getenv("DB_PATH", "./data/monitor.sqlite3")).expanduser()
        return cls(token, admin_id, poll_seconds, max_age, tz, db_path)
