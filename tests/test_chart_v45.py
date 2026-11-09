"""Chart rendering tests with synthetic (never real) sample data."""
import unittest
from datetime import datetime, timedelta
from checkrp.core import UTC
try:
    from checkrp.charts import PERIODS, render_png, render_comparison_png, render_daily_report_png
    import matplotlib
    MISSING = False
except ImportError:
    MISSING = True

@unittest.skipIf(MISSING, 'matplotlib unavailable')
class ChartV45Test(unittest.TestCase):
    def setUp(self):
        now = datetime.now(UTC)
        self.points = [(now-timedelta(minutes=3*(12-i)), 500+i*3, True) for i in range(13)]

    def test_four_periods(self):
        self.assertEqual(set(PERIODS), {'1h','6h','24h','7d'})

    def test_png(self):
        result = render_png(self.points, 'Yuma','1h',1000)
        self.assertTrue(result.startswith(b'\x89PNG'))

    def test_compare_png(self):
        png = render_comparison_png({25:self.points,58:self.points}, '6h',{25:1000,58:750})
        self.assertTrue(png.startswith(b'\x89PNG'))

    def test_daily_png(self):
        stats={sid:dict(n=50,avg_online=600,peak=730,minimum=500,downs=0) for sid in (25,58)}
        self.assertTrue(render_daily_report_png(stats,'TEST').startswith(b'\x89PNG'))
