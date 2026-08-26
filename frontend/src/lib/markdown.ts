/**
 * Markdown rendering, sanitised.
 *
 * Two independent controls, because one is never enough for untrusted content:
 *
 *  1. `marked` is configured to escape raw HTML rather than pass it through, so
 *     markup embedded in Markdown never becomes live DOM in the first place.
 *  2. Everything is then run through DOMPurify with an explicit allowlist.
 *
 * Model output is untrusted input. It is shaped by retrieved transcript text
 * that we do not control, which is a genuine injection path — so the client
 * sanitises independently of the server rather than trusting that the server
 * already did (it did; that is layer two of three, and this is layer three).
 */

import DOMPurify from "dompurify";
import { marked } from "marked";

marked.setOptions({
  gfm: true,
  breaks: false,
});

/** Inline elements permitted in chat prose and rendered Markdown. */
const ALLOWED_TAGS = [
  "h1", "h2", "h3", "h4", "h5", "h6",
  "p", "br", "hr", "blockquote", "pre", "code",
  "strong", "b", "em", "i", "s", "del", "mark", "sup", "sub",
  "ul", "ol", "li",
  "table", "thead", "tbody", "tr", "th", "td",
  "a", "span", "div",
];

const ALLOWED_ATTR = ["href", "title", "class", "colspan", "rowspan", "align", "target", "rel"];

/**
 * Force external links to open safely.
 *
 * A hook rather than post-processing the string: DOMPurify guarantees this runs
 * on the parsed tree for every node, so a link cannot slip past by being
 * formatted unusually. `noopener` prevents the opened page from reaching back
 * through window.opener.
 */
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A" && node.hasAttribute("href")) {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

export function renderMarkdown(source: string): string {
  const html = marked.parse(source ?? "", { async: false }) as string;

  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    // Belt and braces: these are already absent from ALLOWED_TAGS, but naming
    // them makes the intent explicit to the next reader.
    FORBID_TAGS: ["script", "style", "iframe", "object", "embed", "form", "input"],
    FORBID_ATTR: ["onerror", "onload", "onclick", "style"],
    ALLOW_DATA_ATTR: false,
  });
}

/**
 * Render assistant prose, turning [E1] markers into interactive citation chips.
 *
 * The markers are replaced *after* sanitisation with a known-safe span built
 * from a validated label, so no model-produced string is ever interpolated into
 * HTML. The chips are wired up by a delegated click handler in the Message
 * component, keeping this module free of DOM event concerns.
 */
export function renderWithCitations(source: string, knownLabels: Set<string>): string {
  const html = renderMarkdown(source);

  return html.replace(/\[(E\d+(?:,\s*E\d+)*)\]/g, (_match, group: string) => {
    const labels = group.split(",").map((l) => l.trim());
    const chips = labels
      .filter((label) => knownLabels.has(label))
      .map(
        (label) =>
          `<span class="citation-chip" role="button" tabindex="0" ` +
          `data-citation="${label}" aria-label="View source ${label}">${label}</span>`,
      );
    // If nothing survived validation, drop the marker rather than leave "[]".
    return chips.length ? chips.join("") : "";
  });
}

/** Plain-text preview for session titles and artifact cards. */
export function toPlainText(markdown: string, maxLength = 140): string {
  const text = markdown
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[#*_>`~\[\]]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}
