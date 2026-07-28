"""Pure SVG polyline geometry for the dashboard's anomaly-score sparkline
(spec: dashboard-ui Phase 8, "Dashboard anomaly-score chart"; design:
"Server-rendered inline SVG chart").

`build_sparkline()` is a pure, unit-testable function — no FastAPI, no
templates. The `dashboard.html` template renders `Sparkline.points` inside
an inline `<svg><polyline points="..."></svg>`, avoiding any client-side JS
charting library or build step.
"""

from __future__ import annotations

from dataclasses import dataclass

WIDTH = 100
HEIGHT = 30
VIEW_BOX = f"0 0 {WIDTH} {HEIGHT}"


@dataclass(frozen=True)
class Sparkline:
    """SVG polyline geometry derived from an anomaly_score series."""

    points: str
    view_box: str
    empty: bool


def build_sparkline(scores: list[float]) -> Sparkline:
    """Map an `anomaly_score` series onto `WIDTH` x `HEIGHT` SVG coordinates.

    - No scores -> `empty=True`, empty `points` string.
    - One score -> a single centered point (no scale to derive from one
      value).
    - Multiple scores -> x is evenly spaced across `WIDTH` by index; y is
      the score normalized against the series' own min/max, inverted so
      higher scores plot near the top (`y=0`) and lower scores near the
      bottom (`y=HEIGHT`). A flat series (min == max) centers every point
      vertically since there is no range to scale against.
    """
    if not scores:
        return Sparkline(points="", view_box=VIEW_BOX, empty=True)

    count = len(scores)
    minimum = min(scores)
    maximum = max(scores)
    value_range = maximum - minimum

    coordinates: list[str] = []
    for index, score in enumerate(scores):
        x = index / (count - 1) * WIDTH if count > 1 else WIDTH / 2
        if value_range == 0:
            y = HEIGHT / 2
        else:
            normalized = (score - minimum) / value_range
            y = HEIGHT - normalized * HEIGHT
        coordinates.append(f"{x:.2f},{y:.2f}")

    return Sparkline(points=" ".join(coordinates), view_box=VIEW_BOX, empty=False)
