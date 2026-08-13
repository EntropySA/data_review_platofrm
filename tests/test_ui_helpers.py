from ui_helpers import bar_list_html, directional_text_html


def test_directional_text_supports_arabic_and_escapes_uploaded_html():
    rendered = directional_text_html('مرحبا\n<script>alert("x")</script>')

    assert 'dir="auto"' in rendered
    assert "مرحبا" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_bar_list_is_accessible_and_does_not_require_a_chart_library():
    rendered = bar_list_html(
        ({"reviewer": "أحمد <Admin>", "reviews": 3},),
        label_key="reviewer",
        value_key="reviews",
        aria_label="Reviews by reviewer",
    )

    assert 'role="img"' in rendered
    assert 'aria-label="Reviews by reviewer"' in rendered
    assert "أحمد &lt;Admin&gt;" in rendered
    assert ">3<" in rendered
