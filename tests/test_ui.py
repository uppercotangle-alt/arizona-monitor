"""Additional UI smoke tests, run with aiogram installed on the Oracle VPS."""
from __future__ import annotations
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
try:
    from checkrp.ui import kb_home, kb_charts, kb_settings, settings_text, dashboard
    from checkrp.core import UTC, Sample, Snapshot
    from checkrp.db import DB
    HAS_AIOGRAM = True
except ImportError:
    HAS_AIOGRAM = False


@unittest.skipUnless(HAS_AIOGRAM, 'aiogram not available in current test environment')
class UITest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.folder.name) / 'test.sqlite3'))

    def tearDown(self):
        self.db.close()
        self.folder.cleanup()

    def test_no_show_low_callback(self):
        pref = self.db.preference(33)
        for markup in (kb_home(pref), kb_charts()):
            callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
            self.assertFalse(any(':52' in (c or '') for c in callbacks))
        self.assertIn('s:58', [b.callback_data for row in kb_home(pref).inline_keyboard for b in row])

    def test_notification_settings(self):
        pref = self.db.set_enabled(33, True)
        text = settings_text(pref)
        self.assertIn('Vice City', text)
        self.assertIn('735', text)
        rows = kb_settings(pref).inline_keyboard
        self.assertTrue(any(b.callback_data == 'nt:58' for row in rows for b in row))

    def test_dashboard_uses_true_capacity(self):
        now = datetime.now(UTC)
        snapshot = Snapshot({
            25: Sample(25, 'Yuma', 853, 1000, True, now),
            58: Sample(58, 'Vice City', 540, 750, True, now),
        }, now, False, 37)
        text = dashboard(self.db, snapshot)
        self.assertIn('540', text)
        self.assertIn('/750', text)
        self.assertIn('72.0%', text)
        self.assertNotIn('Show Low', text)
