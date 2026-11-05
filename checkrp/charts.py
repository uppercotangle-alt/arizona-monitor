"""PNG online history plot rendered without network or temp files."""
from __future__ import annotations
import io
import os
from datetime import datetime, timedelta
from .core import TZ

PERIODS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}


def render_png(points: list[tuple[datetime, int, bool]], title: str, period: str) -> bytes | None:
    if len(points) < 2:
        return None
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/checkrp-mpl")
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib import dates as mdates
    import math

    # Downsample only for display; database stats still use every observation.
    if len(points) > 650:
        step = max(1, len(points) // 600)
        points = points[::step] + ([points[-1]] if points[-1] not in points[::step] else [])
    timestamps: list[datetime] = []
    values: list[float] = []
    previous: datetime | None = None
    for time_, online, up in points:
        local = time_.astimezone(TZ)
        if previous is not None and (time_ - previous).total_seconds() > 6 * 60:
            timestamps.append(previous.astimezone(TZ) + timedelta(seconds=1))
            values.append(float("nan"))
        timestamps.append(local)
        values.append(float(online) if up else float("nan"))
        previous = time_
    fig, ax = plt.subplots(figsize=(10.4, 4.8), dpi=140)
    try:
        ax.plot(timestamps, values, linewidth=2.2)
        ax.set_ylim(bottom=0)
        ax.set_ylabel("Игроки")
        ax.set_title(f"{title} · {period} · МСК")
        ax.grid(alpha=0.22)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M" if period == "1h" else "%d.%m %H:%M", tz=TZ))
        fig.autofmt_xdate()
        fig.tight_layout()
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png")
        return buffer.getvalue()
    finally:
        plt.close(fig)
