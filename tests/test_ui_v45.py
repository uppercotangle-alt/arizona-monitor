"""Runtime UI and callback-contract checks (run with requirements installed)."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

try:
    from checkrp.core import Sample, Snapshot, UTC
    from checkrp.db import DB
    from checkrp.ui import (
        analytics_text, dashboard, kb_home, kb_analytics, kb_settings, kb_server_settings,
        kb_global_settings, kb_thresholds, kb_report, settings_text, server_settings_text,
        schedule_settings_text, thresholds_text,
    )
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, 'aiogram must be installed for the Telegram UI tests')
class V45UITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.tmp.name)/'v45.sqlite3'))
        self.pref = self.db.preference(123)
        now = datetime.now(UTC)
        self.snap = Snapshot({
            25: Sample(25, 'Yuma', 865, 1000, True, now),
            58: Sample(58, 'Vice City', 720, 750, True, now),
        }, now, False, 37)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_navigation_callbacks_and_lengths(self):
        buttons = []
        for kb in [kb_home(self.pref), kb_analytics(), kb_analytics('58','6h'),
                   kb_settings(self.pref), kb_server_settings(self.pref,25),
                   kb_server_settings(self.pref,58), kb_global_settings(self.pref),
                   kb_thresholds(self.pref), kb_thresholds(self.pref,58), kb_report('7d')]:
            buttons.extend([b.callback_data for row in kb.inline_keyboard for b in row])
        self.assertIn('a:both:24h', buttons)
        self.assertIn('a:58:6h', buttons)
        self.assertIn('g:58:6h', buttons)
        self.assertIn('r:7d', buttons)
        self.assertIn('rp:7d', buttons)
        self.assertIn('toggle:status_58', buttons)
        self.assertIn('nt:global', buttons)
        self.assertNotIn('s:52', buttons)
        self.assertTrue(all(0 < len(c.encode('utf8')) <= 64 for c in buttons))

    def test_real_server_capacity(self):
        text = dashboard(self.db, self.snap)
        self.assertIn('720', text)
        self.assertIn('750', text)
        self.assertIn('96.0%', text)
        self.assertNotIn('Show Low', text)

    def test_new_analytics_variants(self):
        text = analytics_text(self.db, self.snap, None, 'both','6h')
        self.assertIn('6 часов', text)
        self.assertIn('Yuma', text)
        self.assertIn('Vice City', text)
        self.assertIn('нет измерений', text.lower())

    def test_settings_pages_and_opt_in(self):
        general = settings_text(self.pref)
        self.assertIn('УВЕДОМЛЕНИЯ', general)
        self.assertIn('735', general)
        self.assertIn('ВЫКЛЮЧЕНА', general)
        self.assertIn('VICE CITY', server_settings_text(self.pref,58))
        self.assertIn('21:00', schedule_settings_text(self.pref))
        self.assertIn('750', thresholds_text(self.pref))

    def test_messages_below_telegram_limit(self):
        texts = [dashboard(self.db,self.snap), analytics_text(self.db,self.snap,None),
                 settings_text(self.pref),server_settings_text(self.pref,25),
                 schedule_settings_text(self.pref), thresholds_text(self.pref,58)]
        self.assertTrue(all(len(t) < 4096 for t in texts))
