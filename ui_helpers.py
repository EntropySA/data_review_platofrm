"""Safe HTML helpers for bidirectional content and lightweight analytics."""

from html import escape
from typing import Mapping, Sequence


def directional_text_html(value: str) -> str:
    """Render untrusted text with its base direction inferred by the browser."""
    safe_value = escape(value if value else "—")
    return f'<div class="review-text" dir="auto">{safe_value}</div>'


def bar_list_html(
    rows: Sequence[Mapping[str, object]],
    label_key: str,
    value_key: str,
    aria_label: str,
) -> str:
    """Build an accessible bar list without importing a charting dependency."""
    maximum = max((int(row[value_key]) for row in rows), default=0)
    items = []
    for row in rows:
        label = escape(str(row[label_key]))
        value = int(row[value_key])
        width = (value / maximum * 100) if maximum else 0
        items.append(
            """
            <div class="bar-row">
                <div class="bar-label" dir="auto">{label}</div>
                <div class="bar-track" aria-hidden="true">
                    <div class="bar-fill" style="width:{width:.2f}%"></div>
                </div>
                <div class="bar-value">{value}</div>
            </div>
            """.format(label=label, width=width, value=value)
        )
    return (
        '<div class="accessible-chart" role="img" aria-label="{}">{}</div>'.format(
            escape(aria_label, quote=True), "".join(items)
        )
    )
