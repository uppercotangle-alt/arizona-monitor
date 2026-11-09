"""Dark PNG charts for Telegram. No network and no temporary image files."""
from __future__ import annotations
import io
import os
from datetime import datetime, timedelta
from .core import TZ, SERVERS, SERVER_LIMITS

PERIODS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
COLORS = {25: "#F5AE55", 58: "#C58EFF"}
BG = "#101722"
CARD = "#182334"
TEXT = "#EDF3FF"
SUB = "#91A0B7"


def _mpl():
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/checkrp-mpl")
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt, dates as mdates
    return plt, mdates


def _style(ax):
    ax.set_facecolor(CARD)
    ax.tick_params(colors=SUB, labelsize=9, length=0, pad=10)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(axis="y", color="#33415A", alpha=.48, linestyle="--", linewidth=.7)
    ax.set_axisbelow(True)


def _transform(points, percent=False, slots=1000):
    from math import nan
    if len(points) > 650:
        every = max(1, (len(points) + 599) // 600)
        points = points[::every] + ([points[-1]] if points[-1] not in points[::every] else [])
    stamps, values = [], []
    previous = None
    for at, online, up in points:
        if previous and (at - previous).total_seconds() > 360:
            stamps.append(previous.astimezone(TZ) + timedelta(seconds=1))
            values.append(nan)
        stamps.append(at.astimezone(TZ))
        values.append((100 * online / max(1, slots) if percent else online) if up else nan)
        previous = at
    return stamps, values


def _output(fig, plt):
    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", facecolor=BG, dpi=145)
        return buf.getvalue()
    finally:
        plt.close(fig)


def render_png(points: list[tuple[datetime, int, bool]], title: str, period: str,
               max_slots: int | None = None) -> bytes | None:
    if len(points) < 2 or period not in PERIODS:
        return None
    plt, dates = _mpl()
    sid = next((sid for sid, name in SERVERS.items() if name == title), 25)
    color = COLORS[sid]
    stamps, values = _transform(points)
    fig, ax = plt.subplots(figsize=(10, 5.3), facecolor=BG)
    fig.subplots_adjust(top=.78, bottom=.18, left=.1, right=.95)
    _style(ax)
    ax.plot(stamps, values, color=color, linewidth=2.5, zorder=3)
    ax.fill_between(stamps, values, 0, color=color, alpha=.1, zorder=2)
    ax.set_ylim(bottom=0)
    ax.xaxis.set_major_formatter(dates.DateFormatter("%H:%M" if period == "1h" else "%d.%m %H:%M", tz=TZ))
    fig.text(.08, .91, "CHECKRP  /  ONLINE HISTORY", color=SUB, fontsize=10, weight="bold")
    fig.text(.08, .84, f"{title}   ·   {period}", color=TEXT, fontsize=21, weight="bold")
    fig.text(.08, .075, "HTTP API  ·  ВРЕМЯ МСК  ·  ПРОПУСКИ = НЕТ НАДЁЖНЫХ ДАННЫХ", color=SUB, fontsize=9)
    ax.set_ylabel("Игроки", color=SUB, fontsize=10, labelpad=10)
    if max_slots:
        ax.axhline(max_slots, color=SUB, linestyle=":", linewidth=1, alpha=.5)
    return _output(fig, plt)


def render_comparison_png(series: dict[int, list[tuple[datetime, int, bool]]],
                          period: str, limits: dict[int, int]) -> bytes | None:
    if period not in PERIODS or not any(len(v) >= 2 for v in series.values()):
        return None
    plt, dates = _mpl()
    fig, ax = plt.subplots(figsize=(10, 5.5), facecolor=BG)
    fig.subplots_adjust(top=.77, bottom=.18, left=.1, right=.95)
    _style(ax)
    for sid in SERVERS:
        points = series.get(sid, [])
        if len(points) < 2:
            continue
        stamps, values = _transform(points, percent=True, slots=limits.get(sid, 1000))
        ax.plot(stamps, values, color=COLORS[sid], linewidth=2.6, label=SERVERS[sid])
    ax.set_ylim(0, 105)
    ax.set_ylabel("Заполненность, %", color=SUB, fontsize=10)
    ax.xaxis.set_major_formatter(dates.DateFormatter("%H:%M" if period == "1h" else "%d.%m %H:%M", tz=TZ))
    legend = ax.legend(loc="upper left", facecolor=CARD, edgecolor="none", fontsize=10)
    for t in legend.get_texts():
        t.set_color(TEXT)
    fig.text(.08, .91, "CHECKRP  /  SERVER COMPARISON", color=SUB, fontsize=10, weight="bold")
    fig.text(.08, .84, "Yuma  ×  Vice City", color=TEXT, fontsize=21, weight="bold")
    fig.text(.08, .075, "ПРОЦЕНТ ОТ ВМЕСТИМОСТИ СЕРВЕРА  ·  МСК  ·  HTTP API", color=SUB, fontsize=9)
    return _output(fig, plt)


def render_daily_report_png(stats: dict[int, dict], label: str) -> bytes:
    plt, _ = _mpl()
    fig = plt.figure(figsize=(9, 5), facecolor=BG)
    fig.text(.07, .9, "CHECKRP  /  DAILY REPORT", color=SUB, size=12, weight="bold")
    fig.text(.07, .81, label, color=TEXT, size=20, weight="bold")
    for index, sid in enumerate(SERVERS):
        ax = fig.add_axes((.065 + index * .47, .2, .43, .48), facecolor=CARD)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_xticks([]); ax.set_yticks([])
        s = stats[sid]
        avg = f"{s['avg_online']:.0f}" if s['avg_online'] is not None else "—"
        peak = str(s['peak']) if s['peak'] is not None else "—"
        downs = str(s['downs'])
        ax.text(.07, .82, SERVERS[sid].upper(), transform=ax.transAxes, color=COLORS[sid], size=14, weight="bold")
        ax.text(.07, .54, avg, transform=ax.transAxes, color=TEXT, size=35, weight="bold")
        ax.text(.07, .41, "СРЕДНИЙ ОНЛАЙН", transform=ax.transAxes, color=SUB, size=9)
        ax.text(.07, .22, f"Пик: {peak}", transform=ax.transAxes, color=TEXT, size=11)
        ax.text(.07, .1, f"Потерь ответа: {downs}", transform=ax.transAxes, color=SUB, size=10)
    fig.text(.07, .11, "МСК  ·  СТАТИСТИКА НА ОСНОВЕ НАКОПЛЕННЫХ ДАННЫХ API", color=SUB, size=9)
    return _output(fig, plt)
