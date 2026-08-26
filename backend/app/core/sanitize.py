"""HTML sanitisation for generated artifacts.

Generated HTML is **untrusted input**. It is produced by a language model whose
output is influenced by retrieved transcript text, which is itself third-party
content we do not control. That is a genuine injection path: a transcript
containing instruction-shaped text could steer the model toward emitting markup
we would not want to render.

## Defence in depth — three independent layers

1. **The prompt** asks for no JavaScript. This is a *preference*, not a control.
   It reduces noise; it guarantees nothing.
2. **This sanitiser** (server-side) enforces a strict allowlist. It runs before
   storage, so anything read back out of the database or the API — by the UI or
   any future consumer — is already clean.
3. **A sandboxed iframe with a restrictive CSP** (client-side) renders the
   result. Even if layers 1 and 2 both failed, the browser refuses to execute
   scripts, load remote resources, or let the frame touch its parent.

No single layer is trusted. Layer 2 exists specifically so that an artifact
served over the API is safe regardless of how it is rendered.

## Approach: allowlist, not blocklist

Implemented with an allowlist over stdlib's HTMLParser rather than a blocklist
or a regex. Blocklists lose to obfuscation (`<scr<script>ipt>`, entity-encoded
`javascript:`, novel event handlers); an allowlist fails closed — anything not
explicitly permitted is dropped, including tags and attributes invented after
this code was written.

Documented allow/block lists live in architecture.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser

# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

# Structural, textual and tabular markup. Everything a document needs; nothing
# that executes, embeds, or navigates on its own.
ALLOWED_TAGS: frozenset[str] = frozenset({
    "html", "head", "body", "title", "style", "meta",
    "div", "span", "section", "article", "header", "footer", "main", "aside", "nav",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr", "blockquote", "pre", "code", "figure", "figcaption",
    "strong", "b", "em", "i", "u", "s", "small", "mark", "sub", "sup", "abbr", "cite",
    "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
    "a",
})

# Attributes permitted on any allowed tag.
GLOBAL_ATTRS: frozenset[str] = frozenset({"class", "id", "style", "title", "lang", "dir"})

# Per-tag additions.
TAG_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "target", "rel"}),
    "td": frozenset({"colspan", "rowspan", "align"}),
    "th": frozenset({"colspan", "rowspan", "align", "scope"}),
    "col": frozenset({"span"}),
    "colgroup": frozenset({"span"}),
    # charset ONLY. `http-equiv` enables <meta http-equiv="refresh"> redirects,
    # and `name`/`content` have no rendering value in a self-contained document
    # while keeping an attacker-supplied URL in the output. Allowlisting just
    # charset removes the whole class.
    "meta": frozenset({"charset"}),
    "ol": frozenset({"start", "type"}),
}

# Dropped along with everything inside them. `script` and `style` are the two
# tags whose *content* is code rather than text; style is allowed (and its
# content filtered separately), script never is.
VOID_TAGS: frozenset[str] = frozenset({"br", "hr", "meta", "col"})
DROP_WITH_CONTENT: frozenset[str] = frozenset({
    "script", "noscript", "iframe", "frame", "frameset", "object", "embed",
    "applet", "template", "canvas", "svg", "math", "form", "input", "button",
    "select", "textarea", "option", "audio", "video", "source", "track",
    "link", "base", "portal", "dialog",
})

# Only these URL schemes may appear in href. Notably absent: javascript:, data:,
# vbscript:, file:. data: is excluded because `data:text/html,...` in a link is
# a script-execution vector.
SAFE_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})

# CSS constructs that can fetch or execute. `url(` is blocked outright, which
# also removes remote background images — consistent with "no remote resources".
_CSS_FORBIDDEN = re.compile(
    r"(expression\s*\(|javascript\s*:|vbscript\s*:|@import|behavior\s*:|"
    r"-moz-binding|url\s*\(|position\s*:\s*fixed)",
    re.I,
)

# Any attribute starting with `on` is an event handler. Checked as a prefix so
# handlers that do not exist yet are still blocked.
_EVENT_ATTR = re.compile(r"^on", re.I)


@dataclass
class SanitizeReport:
    """What the sanitiser changed. Logged, and surfaced in artifact metadata so
    a user can see that their document was modified and why."""

    removed: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def record(self, what: str) -> None:
        if what not in self.removed:
            self.removed.append(what)
        self.counts[what] = self.counts.get(what, 0) + 1

    @property
    def is_clean(self) -> bool:
        return not self.removed


class _Sanitizer(HTMLParser):
    def __init__(self, report: SanitizeReport) -> None:
        # convert_charrefs=False so entity-encoded payloads are visible to us
        # rather than being silently decoded into something executable.
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.report = report
        # Depth counter rather than a boolean: nested dropped tags must not
        # re-enable output when the inner one closes.
        self._suppress_depth = 0
        self._open: list[str] = []

    # ---- tags ----------------------------------------------------------- #

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()

        if tag in DROP_WITH_CONTENT:
            self.report.record(f"<{tag}>")
            self._suppress_depth += 1
            return
        if self._suppress_depth:
            return
        if tag not in ALLOWED_TAGS:
            # Unknown tag: drop the tag, keep its children. Preserves text
            # content while removing unrecognised markup.
            self.report.record(f"<{tag}> (not allowlisted)")
            return

        rendered = self._render_attrs(tag, attrs)
        self.out.append(f"<{tag}{rendered}>")
        if tag not in VOID_TAGS:
            self._open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag in DROP_WITH_CONTENT:
            if self._suppress_depth:
                self._suppress_depth -= 1
            return
        if self._suppress_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return

        # Only close a tag we actually opened, so malformed input cannot emit
        # stray closing tags that reshape the surrounding document.
        if tag in self._open:
            while self._open:
                open_tag = self._open.pop()
                self.out.append(f"</{open_tag}>")
                if open_tag == tag:
                    break

    def handle_startendtag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            self.report.record(f"<{tag}>")
            return
        if self._suppress_depth or tag not in ALLOWED_TAGS:
            return
        self.out.append(f"<{tag}{self._render_attrs(tag, attrs)}>")

    # ---- content -------------------------------------------------------- #

    def handle_data(self, data: str) -> None:
        if self._suppress_depth:
            return
        # Inside <style>, content is CSS and must be filtered as CSS, not escaped.
        if self._open and self._open[-1] == "style":
            self.out.append(self._clean_css(data))
        else:
            self.out.append(escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if not self._suppress_depth:
            self.out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self._suppress_depth:
            self.out.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        # Comments are dropped entirely: conditional comments were historically
        # a script-execution vector and comments carry no display value.
        self.report.record("<!-- comment -->")

    def handle_decl(self, decl: str) -> None:
        if decl.lower().startswith("doctype"):
            self.out.append(f"<!{decl}>")

    def unknown_decl(self, data: str) -> None:
        # Covers CDATA sections, which can smuggle markup past naive parsers.
        self.report.record("CDATA section")

    def handle_pi(self, data: str) -> None:
        self.report.record("processing instruction")

    # ---- helpers -------------------------------------------------------- #

    def _render_attrs(self, tag: str, attrs) -> str:
        allowed = GLOBAL_ATTRS | TAG_ATTRS.get(tag, frozenset())
        parts: list[str] = []
        saw_target_blank = False

        for name, value in attrs:
            name = (name or "").lower()
            value = value or ""

            if _EVENT_ATTR.match(name):
                self.report.record(f"{name}= (event handler)")
                continue
            if name not in allowed:
                self.report.record(f"{name}= on <{tag}>")
                continue

            if name == "href":
                cleaned = self._clean_url(value)
                if cleaned is None:
                    continue
                value = cleaned
            elif name == "style":
                if _CSS_FORBIDDEN.search(value):
                    self.report.record("style= (unsafe CSS)")
                    continue
            elif name == "target":
                if value.lower() != "_blank":
                    continue
                saw_target_blank = True

            parts.append(f' {name}="{escape(value, quote=True)}"')

        # target="_blank" without noopener leaks a window handle to the opened
        # page. Added unconditionally rather than trusting the model to.
        if tag == "a" and saw_target_blank:
            parts = [p for p in parts if not p.strip().startswith("rel=")]
            parts.append(' rel="noopener noreferrer"')

        return "".join(parts)

    def _clean_url(self, url: str) -> str | None:
        """Allow only safe schemes plus relative/anchor links."""
        # Strip whitespace and control characters: "java\tscript:" is a classic
        # bypass, and browsers ignore those characters when parsing the scheme.
        candidate = re.sub(r"[\s\x00-\x1f]", "", url).strip()

        if not candidate:
            return None
        if candidate.startswith(("#", "/", "./", "../")):
            return candidate
        if ":" not in candidate.split("/")[0]:
            return candidate  # relative path

        scheme = candidate.split(":", 1)[0].lower()
        if scheme in SAFE_SCHEMES:
            return candidate

        self.report.record(f"href scheme '{scheme}:'")
        return None

    def _clean_css(self, css: str) -> str:
        """Filter a <style> block declaration-wise.

        Dropping only the offending declarations, rather than the whole
        stylesheet, keeps a document readable when the model happens to include
        one `url(...)` background among otherwise fine styles.
        """
        if not _CSS_FORBIDDEN.search(css):
            return css

        # At-rules must be removed BEFORE splitting on "}". An @import is
        # terminated by ";" not "}", so it shares a "statement" with whatever
        # rule follows it — an earlier version of this function partitioned on
        # the first "{" and preserved the @import as part of the next rule's
        # selector. Strip them first, by their own terminator.
        css, at_removed = re.subn(
            r"@(?:import|charset|namespace|document)\b[^;{]*(?:;|(?=\{))",
            "",
            css,
            flags=re.I,
        )
        for _ in range(at_removed):
            self.report.record("unsafe CSS at-rule")

        kept = []
        for statement in css.split("}"):
            if not statement.strip():
                continue

            if not _CSS_FORBIDDEN.search(statement):
                kept.append(statement)
                continue

            # Salvage the rule by dropping only the offending declarations —
            # but only if the selector itself is clean. A selector carrying a
            # forbidden construct means the whole rule goes.
            if "{" in statement:
                selector, _, body = statement.partition("{")
                if _CSS_FORBIDDEN.search(selector):
                    self.report.record("unsafe CSS rule")
                    continue
                safe = [
                    d for d in body.split(";") if d.strip() and not _CSS_FORBIDDEN.search(d)
                ]
                self.report.record("unsafe CSS declaration")
                if safe:
                    kept.append(f"{selector}{{{';'.join(safe)};")
                continue

            self.report.record("unsafe CSS declaration")

        return "}".join(kept) + ("}" if kept else "")

    def result(self) -> str:
        # Close anything left open so the output is well-formed even if the
        # model's HTML was not.
        while self._open:
            self.out.append(f"</{self._open.pop()}>")
        return "".join(self.out)


def sanitize_html(html: str) -> tuple[str, SanitizeReport]:
    """Sanitise generated HTML against the allowlist.

    Returns (clean_html, report). Never raises on malformed input — the whole
    point is to accept arbitrary text and return something safe.
    """
    report = SanitizeReport()
    parser = _Sanitizer(report)

    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # pragma: no cover - HTMLParser is very tolerant
        from app.core.logging import artifact_log

        artifact_log.error("sanitize_parser_error", error=str(exc))
        report.record("parser error (content escaped wholesale)")
        return f"<pre>{escape(html)}</pre>", report

    return parser.result(), report


def markdown_is_safe(markdown: str) -> tuple[str, SanitizeReport]:
    """Neutralise raw HTML embedded in Markdown.

    Markdown renderers commonly pass HTML through. Rather than sanitising the
    markup (which would require rendering first), we strip the constructs that
    could execute and leave the Markdown itself untouched. The frontend renders
    Markdown with raw HTML disabled as well — again, two layers.
    """
    report = SanitizeReport()
    cleaned = markdown

    for pattern, label in (
        (r"<script\b[^>]*>.*?</script\s*>", "<script>"),
        (r"<iframe\b[^>]*>.*?</iframe\s*>", "<iframe>"),
        (r"<\s*(?:object|embed|form|link|base)\b[^>]*>", "embedded element"),
        (r"\son[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", "event handler"),
        (r"javascript\s*:", "javascript: URL"),
    ):
        cleaned, count = re.subn(pattern, "", cleaned, flags=re.I | re.S)
        if count:
            for _ in range(count):
                report.record(label)

    return cleaned, report
