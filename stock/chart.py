"""Inline SVG weekly-usage bar chart for the item detail page.

No JS chart library: a handful of <rect> elements is plenty for 12 bars, and
it's just as accessible (a real <svg role="img"> with a text alternative).
"""

from django.utils.html import format_html
from django.utils.safestring import mark_safe

WIDTH = 320
HEIGHT = 100
GAP = 4
BAR_COLOR = "#5980a6"
EXCLUDED_COLOR = "#b3701a"


HATCH_DEF = mark_safe(
    '<defs><pattern id="excluded-week-hatch" width="6" height="6" patternTransform="rotate(45)" '
    'patternUnits="userSpaceOnUse"><rect width="6" height="6" fill="{}"></rect>'
    '<line x1="0" y1="0" x2="0" y2="6" stroke="{}" stroke-width="3"></line></pattern></defs>'.format(
        EXCLUDED_COLOR + "33", EXCLUDED_COLOR
    )
)


def usage_chart_svg(weeks: list[float], excluded_mask: list[bool]) -> str:
    """weeks and excluded_mask are oldest-first, one entry per week shown."""
    n = len(weeks)
    if n == 0:
        return mark_safe('<p class="text-sm text-gray-600">Not enough history for a chart yet.</p>')

    bar_width = (WIDTH - GAP * (n - 1)) / n
    peak = max(weeks) or 1
    bars = []
    for i, (value, excluded) in enumerate(zip(weeks, excluded_mask)):
        bar_height = max(2, round((value / peak) * (HEIGHT - 4)))
        x = i * (bar_width + GAP)
        y = HEIGHT - bar_height
        fill = "url(#excluded-week-hatch)" if excluded else BAR_COLOR
        bars.append(
            format_html(
                '<rect x="{}" y="{}" width="{}" height="{}" fill="{}"></rect>',
                round(x, 1),
                y,
                round(bar_width, 1),
                bar_height,
                mark_safe(fill),
            )
        )

    label = f"Weekly usage for the last {n} weeks"
    svg = format_html(
        '<svg viewBox="0 0 {} {}" role="img" aria-label="{}" class="w-full">{}{}</svg>',
        WIDTH,
        HEIGHT,
        label,
        HATCH_DEF,
        mark_safe("".join(bars)),
    )
    return svg
