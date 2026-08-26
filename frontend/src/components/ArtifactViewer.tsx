/**
 * Artifact Viewer — renders generated Markdown and HTML beside the chat.
 *
 * ## Rendering strategy, by kind
 *
 * **Markdown** is rendered in-page through the sanitising pipeline
 * (lib/markdown.ts): marked escapes raw HTML, then DOMPurify applies an
 * allowlist. It shares the page's origin but can contain no live markup.
 *
 * **HTML** is rendered in a `<iframe>` pointed at the backend's
 * `/api/artifacts/{id}/render` endpoint, NOT via `srcdoc`. This matters:
 *
 *   - `srcdoc` content inherits the parent's origin, so a sandbox escape would
 *     land in our origin with access to localStorage and the session.
 *   - A real URL lets the *server* set `Content-Security-Policy` as a response
 *     header. A `<meta>` CSP inside model-generated markup is part of the
 *     content being defended against.
 *
 * The iframe additionally carries a `sandbox` attribute with no `allow-scripts`
 * and no `allow-same-origin`, so it is a unique opaque origin that cannot run
 * JavaScript even if the server-side sanitiser and CSP both failed.
 *
 * That is three independent layers: prompt → sanitiser+CSP → sandbox.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { renderMarkdown } from "../lib/markdown";
import type { Artifact } from "../lib/types";

interface Props {
  artifact: Artifact;
  onClose: () => void;
  onSave: (content: string) => Promise<void>;
}

type Tab = "preview" | "source";

export default function ArtifactViewer({ artifact, onClose, onSave }: Props) {
  const [tab, setTab] = useState<Tab>("preview");
  const [draft, setDraft] = useState(artifact.content);
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Reset local edit state when a different artifact is opened, otherwise the
  // previous artifact's draft would bleed into the new one.
  useEffect(() => {
    setDraft(artifact.content);
    setTab("preview");
  }, [artifact.id, artifact.content]);

  // Escape closes the panel — expected for any overlay-like surface.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const dirty = draft !== artifact.content;

  const previewHtml = useMemo(
    () => (artifact.kind === "markdown" ? renderMarkdown(draft) : ""),
    [artifact.kind, draft],
  );

  const wordCount = useMemo(() => draft.trim().split(/\s+/).filter(Boolean).length, [draft]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(draft);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard access can be denied; the Source tab is the manual fallback.
      setTab("source");
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(draft);
    } finally {
      setSaving(false);
    }
  };

  const sources = (artifact.metadata?.sources as { guest: string; episode: string; url: string }[]) ?? [];
  const sanitized = artifact.metadata?.sanitized === true;

  return (
    <section className="artifact" aria-label="Artifact viewer">
      <header className="artifact-header">
        <span className="artifact-title" title={artifact.title}>
          {artifact.title}
        </span>

        <span className="artifact-meta">
          {wordCount.toLocaleString()}w
          {artifact.version > 1 && ` · v${artifact.version}`}
        </span>

        <button className="icon-btn" onClick={handleCopy} title="Copy to clipboard">
          {copied ? "✓" : "⧉"}
        </button>

        <a
          className="icon-btn"
          href={api.artifactDownloadUrl(artifact.id)}
          download
          title="Download"
          style={{ textDecoration: "none" }}
        >
          ↓
        </a>

        {dirty && (
          <button className="icon-btn" onClick={handleSave} disabled={saving} title="Save edits">
            {saving ? "…" : "Save"}
          </button>
        )}

        <button className="icon-btn" onClick={onClose} title="Close" ref={closeRef}>
          ✕
        </button>
      </header>

      <div className="artifact-tabs" role="tablist">
        <button
          className="artifact-tab"
          role="tab"
          aria-selected={tab === "preview"}
          onClick={() => setTab("preview")}
        >
          Preview
        </button>
        <button
          className="artifact-tab"
          role="tab"
          aria-selected={tab === "source"}
          onClick={() => setTab("source")}
        >
          {artifact.kind === "html" ? "HTML" : "Markdown"}
        </button>
      </div>

      <div className="artifact-body">
        {tab === "source" ? (
          <textarea
            className="artifact-source"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            spellCheck={false}
            aria-label="Artifact source"
          />
        ) : artifact.kind === "html" ? (
          <iframe
            className="artifact-frame"
            /* Loaded from a real URL so the server can set CSP headers, and
               isolated by an empty-ish sandbox: no allow-scripts (JS cannot
               run) and no allow-same-origin (opaque origin, no access to our
               storage or cookies). allow-popups only so a link can open. */
            src={api.artifactRenderUrl(artifact.id)}
            sandbox="allow-popups allow-popups-to-escape-sandbox"
            referrerPolicy="no-referrer"
            title={`Rendered artifact: ${artifact.title}`}
            loading="lazy"
          />
        ) : (
          <article
            className="artifact-prose"
            dangerouslySetInnerHTML={{ __html: previewHtml }}
          />
        )}
      </div>

      {sources.length > 0 && tab === "preview" && (
        <details style={{ borderTop: "1px solid var(--border)", padding: "8px 14px" }}>
          <summary
            style={{ fontSize: 12, color: "var(--text-muted)", cursor: "pointer" }}
          >
            Grounded in {sources.length} transcript{sources.length === 1 ? "" : "s"}
          </summary>
          <ul style={{ margin: "8px 0 4px", paddingLeft: 18, fontSize: 12 }}>
            {sources.map((source, index) => (
              <li key={index} style={{ marginBottom: 4, color: "var(--text-muted)" }}>
                <strong>{source.guest}</strong>
                {source.url && (
                  <>
                    {" — "}
                    <a href={source.url} target="_blank" rel="noopener noreferrer">
                      {source.episode}
                    </a>
                  </>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="artifact-security-note">
        <span aria-hidden="true">🔒</span>
        {artifact.kind === "html" ? (
          <span>
            Rendered in a sandboxed frame — scripts blocked, no network access
            {sanitized && ", unsafe markup removed"}.
          </span>
        ) : (
          <span>Markdown sanitised — embedded HTML and scripts are inert.</span>
        )}
      </div>
    </section>
  );
}
