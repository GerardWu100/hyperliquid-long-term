"""Generate the technical charts used by the bilingual blog post.

Inputs are frozen CSV files under ``blog/data``. The script writes two PNG
figures under ``blog/images`` and does not connect to ClickHouse or Hyperliquid.
The threshold data comes from the project's ``config.toml``. Compression values
come from the measured crypto-perpetual sample in ``COMPRESSION_BENCHMARK.md``;
they are not presented as production Hyperliquid table measurements.
"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

BLOG_ROOT = Path(__file__).resolve().parent
DATA_DIR = BLOG_ROOT / "data"
IMAGE_DIR = BLOG_ROOT / "images"
FIGURE_DPI = 180
NAVY = "#081426"
CYAN = "#18BBD2"
VIOLET = "#7566E8"
AMBER = "#F2A93B"
CORAL = "#E4665C"
MUTED = "#8290A6"


def _read_csv(path: Path) -> list[dict[str, str]]:
    """Read a frozen comma-separated values file into row dictionaries.

    Parameters
    ----------
    path:
        Absolute path to a UTF-8 CSV file with one header row.

    Returns
    -------
    list[dict[str, str]]
        Rows keyed by their column names. All values remain strings until the
        chart-specific function converts numeric columns explicitly.
    """
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def plot_freshness_timeline() -> None:
    """Plot configured alert thresholds against the nominal REST horizon.

    The x-axis is downtime in hours. Points correspond to warning, serious,
    urgent, and critical freshness alerts plus the first open in a 5,000-slot
    inclusive candle window, which lies 4,999 minutes behind the final open.
    The shaded final interval shows the configured repair margin between the
    critical alert and the nominal horizon.
    """
    rows = _read_csv(DATA_DIR / "freshness_thresholds.csv")
    labels = [row["stage"] for row in rows]
    minutes = [int(row["minutes"]) for row in rows]
    hours = [value / 60 for value in minutes]
    colors = [CYAN, VIOLET, AMBER, CORAL, MUTED]

    figure, axis = plt.subplots(figsize=(12, 4.8), constrained_layout=True)
    figure.patch.set_facecolor("white")
    axis.set_facecolor("#F6F8FB")
    axis.hlines(0, 0, hours[-1], color=NAVY, linewidth=2)
    axis.scatter(hours, [0] * len(hours), s=150, color=colors, zorder=3)

    critical_hours = hours[-2]
    horizon_hours = hours[-1]
    axis.axvspan(critical_hours, horizon_hours, color=CORAL, alpha=0.12)
    axis.text(
        (critical_hours + horizon_hours) / 2,
        0.30,
        "11 h 19 min configured repair margin",
        ha="center",
        va="bottom",
        color=CORAL,
        fontsize=10,
        fontweight="bold",
    )

    for index, (label, hour, minute, color) in enumerate(
        zip(labels, hours, minutes, colors, strict=True)
    ):
        y_text = -0.30 if index % 2 == 0 else 0.16
        axis.text(
            hour,
            y_text,
            f"{label}\n{minute:,} min",
            ha="center",
            va="top" if y_text < 0 else "bottom",
            color=color,
            fontsize=9,
            fontweight="bold",
        )

    axis.set_xlim(-2, horizon_hours + 2)
    axis.set_ylim(-0.65, 0.65)
    axis.set_yticks([])
    axis.set_xlabel("Minutes since the latest stored candle (hours)")
    axis.set_title(
        "Freshness alerts are staged before REST recovery runs out",
        loc="left",
        fontsize=15,
        fontweight="bold",
        color=NAVY,
    )
    axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0f}h"))
    axis.grid(axis="x", color="#D8DEE9", linewidth=0.8)
    for spine in axis.spines.values():
        spine.set_visible(False)

    figure.savefig(IMAGE_DIR / "01_freshness_timeline.png", dpi=FIGURE_DPI)
    plt.close(figure)


def plot_compression_benchmark() -> None:
    """Plot measured compressed bytes per row for four codec variants.

    Data represents the repository's 3,162,240-row crypto perpetual sample.
    The measure is compressed bytes per row, where lower values are better.
    """
    rows = _read_csv(DATA_DIR / "compression_benchmark.csv")
    labels = [row["variant"] for row in rows]
    values = [float(row["bytes_per_row"]) for row in rows]
    colors = [CYAN, MUTED, VIOLET, CORAL]

    figure, axis = plt.subplots(figsize=(11, 6), constrained_layout=True)
    figure.patch.set_facecolor("white")
    bars = axis.barh(labels, values, color=colors, height=0.62)
    axis.invert_yaxis()
    axis.bar_label(bars, fmt="%.2f B/row", padding=6, color=NAVY, fontsize=10)
    axis.set_xlim(0, max(values) * 1.18)
    axis.set_xlabel("Compressed bytes per row (lower is better)")
    axis.set_title(
        "Codec choice changes storage cost",
        loc="left",
        fontsize=15,
        fontweight="bold",
        color=NAVY,
        pad=22,
    )
    axis.text(
        0,
        1.005,
        "Crypto perpetual sample: 3,162,240 one-minute rows",
        transform=axis.transAxes,
        color=MUTED,
        fontsize=10,
        va="bottom",
    )
    axis.grid(axis="x", color="#D8DEE9", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.tick_params(axis="y", length=0)

    figure.savefig(IMAGE_DIR / "02_compression_benchmark.png", dpi=FIGURE_DPI)
    plt.close(figure)


def main() -> None:
    """Create every deterministic chart referenced by the blog post."""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    plot_freshness_timeline()
    plot_compression_benchmark()


if __name__ == "__main__":
    main()
