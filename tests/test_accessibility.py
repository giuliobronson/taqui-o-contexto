"""WCAG AA contrast of the theme tokens and the ranking colours (T-35)."""

import colorsys
import re
from pathlib import Path

import pytest

from app.guesses.presentation import rank_to_color

CSS = (Path(__file__).resolve().parent.parent / "static/css/app.css").read_text()
TOKENS = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6});", CSS))


def _rgb(value: str) -> tuple[float, float, float]:
    if value.startswith("#"):
        return tuple(int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]
    h, s, lightness = map(float, re.findall(r"[\d.]+", value))
    return colorsys.hls_to_rgb(h / 360, lightness / 100, s / 100)


def _luminance(rgb: tuple[float, float, float]) -> float:
    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = map(channel, rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(_rgb(a)), _luminance(_rgb(b))), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize(
    ("fg", "bg"),
    [
        ("text", "bg"),
        ("text", "surface"),
        ("text-muted", "bg"),
        ("text-muted", "surface"),
        ("text-muted", "surface-muted"),
        ("accent-text", "accent"),  # primary buttons
        ("accent", "surface"),  # focus ring and links
        ("danger", "surface-muted"),  # "Apagar" link on a hint
    ],
)
def test_theme_text_meets_aa(fg, bg) -> None:
    assert contrast(TOKENS[fg], TOKENS[bg]) >= 4.5


def test_white_on_danger_button() -> None:
    assert contrast("#ffffff", TOKENS["danger"]) >= 4.5


@pytest.mark.parametrize("rank", [1, 2, 10, 100, 1_000, 10_000, 40_000])
def test_ranking_rows_are_readable(rank) -> None:
    assert contrast(TOKENS["text"], rank_to_color(rank, 40_000)) >= 7
