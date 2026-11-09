import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from checkrp.core import (TZ, UTC, MAX_DATA_AGE, Sample, Snapshot, dt_parse,
                          parse_api, payday_reminder, next_payday, daily_window, slot_cross)
from checkrp.db import DB


def make(sid=25, at=None, responding=True, online=990):
    return Sample(sid, "Yuma" if sid == 25 else "Vice City", online, (1000 if sid == 25 else 750),
                  responding, at or datetime(2026, 11, 5, 12, 0, tzinfo=UTC))


def snapshot(*samples, now=None, source_problem=False):
    return Snapshot({s.server_id: s for s in samples}, now or samples[0].source_at,
                    source_problem, 37)


class CoreTest(unittest.TestCase):
    def test_parse_realistic_api(self):
        now = datetime(2026, 11, 5, 10, 12, 20, tzinfo=UTC)
        data = {"servers": [{"id": 25, "name": "Yuma", "online": 747,
                             "max_slots": 1000, "responding": True,
                             "updated_at": "2026-11-05T10:12:01Z", "peak_hour": 754, "peak_day": 887},
                            {"id": 58, "name": "Vice City", "online": 259,
                             "max_slots": 750, "responding": True,
                             "updated_at": "2026-11-05T10:12:01Z"}]}
        data["servers"] += [{"id": i, "responding": True} for i in range(100, 110)]
        result = parse_api(data, now)
        self.assertEqual(result.servers[25].online, 747)
        self.assertEqual(result.servers[58].name, "Vice City")
        self.assertTrue(result.servers[25].fresh(now))
        self.assertFalse(result.source_problem)

    def test_invalid_api(self):
        with self.assertRaises(ValueError):
            parse_api({"servers": []}, datetime.now(UTC))
        with self.assertRaises(ValueError):
            dt_parse("2026-11-05 10:00:00")

    def test_stale_future(self):
        now = datetime(2026, 11, 5, 12, 5, tzinfo=UTC)
        self.assertFalse(make(at=now-timedelta(minutes=4)).fresh(now))
        self.assertFalse(make(at=now+timedelta(minutes=2)).fresh(now))

    def test_payday_moscow(self):
        local = ZoneInfo("Europe/Moscow")
        for minute, expected in ((20, "12:30"), (50, "13:00")):
            now = datetime(2026, 11, 5, 12, minute, 25, tzinfo=local)
            result = payday_reminder(now)
            self.assertIsNotNone(result)
            self.assertEqual(result[1].strftime("%H:%M"), expected)
        self.assertIsNone(payday_reminder(datetime(2026, 11, 5, 12, 21, tzinfo=local)))
        self.assertEqual(next_payday(datetime(2026, 11, 5, 12, 30, tzinfo=local)).strftime("%H:%M"), "13:00")

    def test_daily_report_window(self):
        loc = ZoneInfo("Europe/Moscow")
        start, end = daily_window(datetime(2026, 11, 5, 21, 3, tzinfo=loc))
        self.assertEqual(start.astimezone(loc).strftime("%H:%M"), "00:00")
        self.assertIsNone(daily_window(datetime(2026, 11, 5, 20, 59, tzinfo=loc)))
        self.assertIsNone(daily_window(datetime(2026, 11, 5, 21, 15, tzinfo=loc)))

    def test_slot_crossing(self):
        self.assertEqual(slot_cross(None, 979), (True, True))
        self.assertEqual(slot_cross(True, 978), (False, True))
        self.assertEqual(slot_cross(True, 980), (False, False))
        self.assertEqual(slot_cross(False, 979), (True, True))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "test.sqlite3")
        self.db = DB(self.path)
        self.base = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def ingest(self, minute, status=True, online=990, sid=25, *, source_problem=False, observed_shift=0):
        ts = self.base + timedelta(minutes=minute)
        sample = make(sid, ts, status, online)
        now = ts + timedelta(seconds=observed_shift)
        return self.db.collect(snapshot(sample, now=now, source_problem=source_problem))

    def test_confirmed_down_up_two_DISTINCT_timestamps(self):
        events, fresh = self.ingest(0, True)
        self.assertEqual(events, [])
        self.assertEqual(len(fresh), 1)
        self.assertEqual(self.ingest(1, False)[0], [])
        # Repeated cached responses should never confirm a restart
        self.assertEqual(self.ingest(1, False)[0], [])
        down = self.ingest(2, False)[0]
        self.assertEqual(len(down), 1)
        self.assertEqual(down[0].kind, "down")
        self.assertEqual(self.ingest(3, True)[0], [])
        up = self.ingest(4, True)[0]
        self.assertEqual(len(up), 1)
        self.assertEqual(up[0].kind, "up")
        self.assertEqual(up[0].duration_seconds, 3*60)
        self.assertEqual(len(self.db.recent_events()), 2)

    def test_stale_data_and_mass_outage_not_events(self):
        self.ingest(0)
        self.ingest(1, False, observed_shift=500)  # stale >180s
        events, samples = self.ingest(2, False, source_problem=True)
        self.assertEqual((events, samples), ([], []))
        self.assertEqual(self.ingest(3, False)[0], [])  # first valid false
        self.assertEqual(len(self.ingest(4, False)[0]), 1)

    def test_state_survives_restart(self):
        self.ingest(0, True)
        self.ingest(1, False)
        self.db.close()
        self.db = DB(self.path)
        down = self.ingest(2, False)[0]
        self.assertEqual(len(down), 1)

    def test_preferences_and_slots_no_spam(self):
        self.db.set_enabled(345, True)
        self.db.toggle(345, "slots_25")
        self.assertEqual(self.db.slot_alert_chats(make(25, self.base, True, 979)), [(345, 980)])
        self.assertEqual(self.db.slot_alert_chats(make(25, self.base, True, 978)), [])
        self.assertEqual(self.db.slot_alert_chats(make(25, self.base, True, 980)), [])
        self.assertEqual(self.db.slot_alert_chats(make(25, self.base, True, 979)), [(345, 980)])
        self.assertEqual(self.db.slot_alert_chats(make(58, self.base, True, 700)), [])
        self.db.set_enabled(345, False)
        self.assertEqual(self.db.subscribers(), [])

    def test_10_minute_delta(self):
        self.ingest(0, True, online=800)
        for minute in range(1, 12):
            self.ingest(minute, True, online=800 + minute)
        latest = make(25, self.base + timedelta(minutes=11), True, 811)
        self.assertEqual(self.db.last_10min_delta(25, latest)[0], 10)

    def test_report_stats(self):
        self.ingest(0, True, 900)
        self.ingest(1, True, 950)
        self.ingest(2, False, 0)
        stats = self.db.stats(25, self.base, self.base+timedelta(minutes=3))
        self.assertEqual(stats["n"], 3)
        self.assertEqual(stats["avg_online"], 925)
        self.assertEqual(stats["answered"], 2)

    def test_dedup_notifications(self):
        self.assertFalse(self.db.was_sent(123, "payday", "2026-11-05 12:30"))
        self.db.mark_sent(123, "payday", "2026-11-05 12:30")
        self.assertTrue(self.db.was_sent(123, "payday", "2026-11-05 12:30"))


if __name__ == "__main__":
    unittest.main()

class ExtraOutageTests(unittest.TestCase):
    def test_downtime_duration_stats(self):
        with tempfile.TemporaryDirectory() as folder:
            db = DB(str(Path(folder) / "x.sqlite3"))
            try:
                base = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)
                for minute, up in [(0, True), (1, False), (2, False), (3, True), (4, True)]:
                    t = base + timedelta(minutes=minute)
                    db.collect(snapshot(make(25, t, up, 500), now=t))
                data = db.stats(25, base, base + timedelta(minutes=5))
                self.assertEqual(data["downtime_seconds"], 3 * 60)
                self.assertEqual(data["longest_downtime_seconds"], 3 * 60)
                self.assertEqual(data["downs"], 1)
            finally:
                db.close()

class MigrationTest(unittest.TestCase):
    def test_preserves_archived_history_and_legacy_flags(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'legacy.sqlite3')
            conn = sqlite3.connect(path)
            conn.executescript('''
                CREATE TABLE prefs(chat_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
                    status_25 INTEGER NOT NULL DEFAULT 1, status_52 INTEGER NOT NULL DEFAULT 1,
                    slots_25 INTEGER NOT NULL DEFAULT 0, slots_52 INTEGER NOT NULL DEFAULT 0,
                    payday INTEGER NOT NULL DEFAULT 0, daily INTEGER NOT NULL DEFAULT 0);
                INSERT INTO prefs VALUES(999,1,1,1,1,1,1,1);
                CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER,
                    kind TEXT, happened_at TEXT, since_at TEXT, duration_seconds INTEGER);
                INSERT INTO events(server_id,kind,happened_at) VALUES (52,'down','2026-11-05T12:00:00+00:00');
            ''')
            conn.close()
            db = DB(path)
            try:
                pref = db.preference(999)
                self.assertTrue(pref.status_25)
                self.assertFalse(pref.status_58)
                self.assertFalse(pref.slots_58)
                self.assertTrue(pref.payday)
                self.assertTrue(pref.daily)
                self.assertEqual(pref.threshold_58, 735)
                self.assertEqual(len(db.recent_events(archive=True)), 1)
                self.assertEqual(db.recent_events(), [])
                # Migration is idempotent: the DB survives a restart intact.
                db.close()
                db = DB(path)
                self.assertEqual(db.preference(999).threshold_25, 980)
            finally:
                db.close()

    def test_vice_city_thresholds_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            db = DB(str(Path(folder) / 'db.sqlite3'))
            try:
                db.set_enabled(777, True)
                db.toggle(777, 'slots_58')
                sample = make(58, datetime(2026, 11, 9, 17, 0, tzinfo=UTC), True, 734)
                self.assertEqual(db.slot_alert_chats(sample), [(777, 735)])
                self.assertEqual(db.slot_alert_chats(sample), [])
                db.set_threshold(777, 58, 740)
                self.assertEqual(db.slot_alert_chats(sample), [(777, 740)])
                self.assertEqual(db.slot_alert_chats(sample), [])
                self.assertEqual(db.slot_alert_chats(make(25, sample.source_at, True, 979)), [])
                with self.assertRaises(ValueError):
                    db.set_threshold(777, 58, 1000)
                with self.assertRaises(ValueError):
                    db.toggle(777, 'slots_52')
            finally:
                db.close()
