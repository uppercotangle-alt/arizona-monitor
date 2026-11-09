"""CheckRP 4.5 premium dark charts. Draws PNG bytes in memory; no Web App.

Plots only observed responding data. Missing / stale feed is a gap, not 0.
"""
from __future__ import annotations

import io
import math
import os
from datetime import datetime, timedelta
from .core import TZ, SERVERS

PERIODS = {"1h": timedelta(hours=1), "6h": timedelta(hours=6),
           "24h": timedelta(hours=24), "7d": timedelta(days=7)}
COLOR = {25: "#F3AA53", 58: "#B887FF"}
BG = "#0C1420"
PANEL = "#151F2F"
LINE = "#26364C"
TEXT = "#F3F6FF"
SOFT = "#91A3BB"
TEAL = "#69D0BD"


def _mpl():
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/checkrp-mpl")
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt, dates as mdates
    from matplotlib.patches import FancyBboxPatch
    plt.rcParams["font.family"] = "DejaVu Sans"
    plt.rcParams["axes.unicode_minus"] = False
    return plt, mdates, FancyBboxPatch


def _base(title: str, tag: str, subtitle: str):
    plt, dates, Box = _mpl()
    fig = plt.figure(figsize=(12, 6.8), facecolor=BG)
    fig.text(.073, .927, "CHECKRP", color=TEXT, size=18, weight="bold", va="center")
    fig.text(.187, .927, "  /  " + tag.upper(), color=TEAL, size=11, weight="bold", va="center")
    fig.text(.074, .849, title, color=TEXT, size=24, weight="bold", va="center")
    fig.text(.075, .795, subtitle, color=SOFT, size=10.5, va="center")
    fig.add_artist(plt.Line2D([.075, .925], [.757, .757], transform=fig.transFigure,
                              color=LINE, linewidth=1.2))
    fig.text(.075, .051, "HTTP API • g-rp.me", color=SOFT, size=9)
    fig.text(.925, .051, "МОСКОВСКОЕ ВРЕМЯ  •  МСК", color=SOFT, size=9, ha="right")
    return fig, plt, dates, Box


def _metric(fig, Box, x: float, width: float, label: str, value: str, color: str = TEXT):
    fig.add_artist(Box((x, .621), width, .112, boxstyle="round,pad=0.007,rounding_size=0.012",
                       facecolor=PANEL, linewidth=0, transform=fig.transFigure))
    fig.text(x + .017, .69, label.upper(), color=SOFT, size=9, weight="semibold")
    fig.text(x + .017, .647, value, color=color, size=18, weight="bold")


def _axes(fig):
    ax = fig.add_axes([.078, .157, .847, .412], facecolor=PANEL)
    ax.patch.set_edgecolor('none')
    ax.grid(axis="y", color=LINE, linestyle="--", linewidth=.85, alpha=.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=SOFT, labelsize=9, length=0, pad=11)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.margins(x=.013)
    return ax


def _transform(points, percent=False, slots=1000):
    # Keep graph rendering bounded for heavily sampled multi-day windows.
    if len(points) > 900:
        step = math.ceil(len(points) / 850)
        subset = points[::step]
        if subset[-1] != points[-1]:
            subset.append(points[-1])
        points = subset
    xs, ys = [], []
    last_t = None
    for at, online, responding in points:
        if last_t and (at-last_t).total_seconds() > 360:
            xs.append(last_t.astimezone(TZ) + timedelta(seconds=1))
            ys.append(float('nan'))
        xs.append(at.astimezone(TZ))
        ys.append((100*online/max(1, slots) if percent else online) if responding else float('nan'))
        last_t = at
    return xs, ys


def _finalize(fig, plt):
    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", dpi=115, facecolor=BG)
        return buf.getvalue()
    finally:
        plt.close(fig)


def render_png(points: list[tuple[datetime, int, bool]], title: str, period: str,
               max_slots: int | None = None) -> bytes | None:
    if len(points) < 2 or period not in PERIODS:
        return None
    sid = next((k for k, v in SERVERS.items() if v == title), 25)
    col = COLOR[sid]
    values = [online for _, online, up in points if up]
    if not values:
        return None
    current = values[-1]
    capacity = max_slots or (1000 if sid == 25 else 750)
    fig, plt, dates, Box = _base(f"{title}  /  Онлайн", "history",
                                 f"Динамика · {period}     •     Пропуски не заменяются нулями")
    _metric(fig, Box, .075, .195, "Последнее измерение", f"{current:,}".replace(",", " "), col)
    _metric(fig, Box, .286, .195, "Средний онлайн", f"{sum(values)/len(values):,.0f}".replace(",", " "))
    _metric(fig, Box, .496, .195, "Пик", f"{max(values):,}".replace(",", " "))
    _metric(fig, Box, .705, .217, "Заполненность", f"{current / capacity * 100:.1f}%", TEAL)
    ax = _axes(fig)
    xs, ys = _transform(points)
    ax.plot(xs, ys, color=col, linewidth=2.9, zorder=3, solid_capstyle='round')
    ax.fill_between(xs, ys, 0, color=col, alpha=.13, zorder=2)
    ax.scatter([points[-1][0].astimezone(TZ)], [points[-1][1]], color=col, s=55, zorder=5,
               edgecolors=BG, linewidths=1.5) if points[-1][2] else None
    ax.set_ylim(0, max(capacity * 1.08, max(values)*1.09))
    ax.set_ylabel("ИГРОКИ", color=SOFT, size=9, labelpad=14)
    ax.xaxis.set_major_formatter(dates.DateFormatter("%H:%M" if period in ("1h", "6h") else "%d.%m  %H:%M", tz=TZ))
    ax.xaxis.set_major_locator(dates.AutoDateLocator(minticks=3, maxticks=7))
    fig.text(.075, .095, f"ЛИМИТ СЕРВЕРА: {capacity:,}".replace(",", " "), color=SOFT, size=8.5)
    return _finalize(fig, plt)


def render_comparison_png(series: dict[int, list[tuple[datetime, int, bool]]],
                          period: str, limits: dict[int, int]) -> bytes | None:
    if period not in PERIODS or not any(len(v) >= 2 for v in series.values()):
        return None
    fig, plt, dates, Box = _base("Yuma  ×  Vice City", "head to head",
                                 f"Сравнение заполненности · {period} · 100% = все слоты заняты")
    for i, sid in enumerate(SERVERS):
        points = series.get(sid, [])
        vals = [n / max(1, limits.get(sid, 1000))*100 for _, n, up in points if up]
        _metric(fig, Box, .075 + i*.426, .402, f"{SERVERS[sid]}  /  последний %",
                f"{vals[-1]:.1f}%" if vals else "нет измерений", COLOR[sid])
    ax = _axes(fig)
    lines = 0
    for sid in SERVERS:
        points = series.get(sid, [])
        if len(points) < 2:
            continue
        xs, ys = _transform(points, True, limits.get(sid, 1000))
        ax.plot(xs, ys, color=COLOR[sid], linewidth=2.8, label=SERVERS[sid], solid_capstyle='round')
        lines += 1
    if not lines:
        plt.close(fig)
        return None
    ax.set_ylim(0, 105)
    ax.set_ylabel("ЗАПОЛНЕННОСТЬ, %", color=SOFT, size=9, labelpad=14)
    ax.xaxis.set_major_formatter(dates.DateFormatter("%H:%M" if period in ("1h", "6h") else "%d.%m  %H:%M", tz=TZ))
    ax.xaxis.set_major_locator(dates.AutoDateLocator(minticks=3, maxticks=7))
    lg = ax.legend(loc="upper left", frameon=True, facecolor=PANEL, edgecolor=LINE, fontsize=10)
    for label in lg.get_texts():
        label.set_color(TEXT)
    fig.text(.075, .095, "ПРОЦЕНТ ОТ 1000 / 750 СЛОТОВ", color=SOFT, size=8.5)
    return _finalize(fig, plt)


def render_daily_report_png(stats: dict[int, dict], label: str) -> bytes:
    fig, plt, _, Box = _base("Отчёт по серверам", "daily intelligence", label)
    for i, sid in enumerate(SERVERS):
        x = .075 + i*.426
        fig.add_artist(Box((x, .21), .405, .52, boxstyle="round,pad=0.009,rounding_size=0.02",
                           facecolor=PANEL, linewidth=0, transform=fig.transFigure))
        s = stats[sid]
        fig.text(x + .032, .677, SERVERS[sid].upper(), color=COLOR[sid], size=15, weight="bold")
        average = f"{s['avg_online']:.0f}" if s['avg_online'] is not None else "—"
        fig.text(x + .032, .549, average, color=TEXT, size=36, weight="bold")
        fig.text(x + .032, .506, "СРЕДНИЙ ОНЛАЙН", color=SOFT, size=9)
        fig.add_artist(plt.Line2D([x+.033,x+.371], [.462,.462], color=LINE,
                                   linewidth=1, transform=fig.transFigure))
        for y, text in ((.404, f"Пик     {s['peak'] if s['peak'] is not None else '—'}"),
                        (.349, f"Мин.    {s['minimum'] if s['minimum'] is not None else '—'}"),
                        (.294, f"События недоступности    {s['downs']}"),
                        (.239, f"Точек измерения    {s['n']}")):
            fig.text(x+.032, y, text, color=TEXT if y>=.35 else SOFT, size=10)
    fig.text(.075, .118, "ДАННЫЕ ИСТОРИИ HTTP API • НЕДОСТУПНОСТЬ НЕ РАВНА РЕСТАРТУ", color=SOFT, size=9)
    return _finalize(fig, plt)
