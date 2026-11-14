"""Small presentation helpers for the job-shop notebook."""

from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns
from IPython.display import Markdown, display

COLORS = {"A": "#287c67", "B": "#486eb0", "C": "#b67336"}


def theme() -> None:
    """Apply a consistent theme to embedded charts."""
    sns.set_theme(style="whitegrid", context="notebook", font="DejaVu Sans")
    plt.rcParams.update(
        {
            "figure.dpi": 130,
            "text.parse_math": False,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": "#e2e8e4",
        }
    )


def prose(text: str) -> None:
    """Render result-derived Markdown."""
    display(Markdown(text))


def finish(fig: plt.Figure, title: str, subtitle: str, filename: str) -> None:
    """Embed a chart and save it for visual review."""
    fig.suptitle(title, x=0.07, y=0.98, ha="left", fontsize=18, weight="bold", color="#253d31")
    fig.text(0.07, 0.90, subtitle, ha="left", fontsize=11, color="#52675a")
    fig.text(
        0.07,
        0.025,
        "Source: synthetic job-shop fixture · local vgi-ortools / CP-SAT · all durations and times in minutes",
        ha="left",
        fontsize=9,
        color="#52675a",
    )
    fig.subplots_adjust(left=0.12, right=0.97, bottom=0.30, top=0.79)
    directory = Path("validation/charts")
    directory.mkdir(parents=True, exist_ok=True)
    fig.savefig(directory / filename, dpi=150)
    plt.show()
