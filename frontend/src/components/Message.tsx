/**
 * One chat message.
 *
 * Assistant prose is rendered through the sanitising Markdown pipeline, with
 * [E1] markers converted to clickable citation chips. Clicks are handled by
 * delegation on the container rather than by attaching listeners to injected
 * nodes — the HTML is set via dangerouslySetInnerHTML, so React never owns
 * those elements and cannot bind props to them.
 */

import { useCallback, useMemo, useState } from "react";
import { renderWithCitations } from "../lib/markdown";
import type { Citation } from "../lib/types";
import Sources from "./Sources";

interface Props {
  role: "user" | "assistant" | "system";
  content: string;
  citations: Citation[];
  insufficientEvidence?: boolean;
  provider?: string | null;
  model?: string | null;
  latencyMs?: number | null;
  /** True while tokens are still arriving, to show the caret. */
  streaming?: boolean;
}

export default function Message({
  role,
  content,
  citations,
  insufficientEvidence,
  provider,
  model,
  latencyMs,
  streaming,
}: Props) {
  const [focusedLabel, setFocusedLabel] = useState<string | null>(null);

  const knownLabels = useMemo(
    () => new Set(citations.map((c) => c.label)),
    [citations],
  );

  const html = useMemo(() => {
    if (role === "user") return null;
    return renderWithCitations(content, knownLabels);
  }, [content, role, knownLabels]);

  const handleClick = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    const label = target.dataset?.citation;
    if (label) setFocusedLabel(label);
  }, []);

  // Chips are role="button" with tabindex, so they must respond to keyboard
  // activation as well as clicks.
  const handleKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const target = event.target as HTMLElement;
    const label = target.dataset?.citation;
    if (label) {
      event.preventDefault();
      setFocusedLabel(label);
    }
  }, []);

  if (role === "user") {
    return (
      <div className="message user">
        <div className="message-role">You</div>
        <div className="message-body">{content}</div>
      </div>
    );
  }

  return (
    <div className="message assistant">
      <div className="message-role">
        <span>Assistant</span>
        {model && (
          <span style={{ fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>
            · {model}
            {typeof latencyMs === "number" && ` · ${(latencyMs / 1000).toFixed(1)}s`}
          </span>
        )}
      </div>

      <div
        className={`message-body ${insufficientEvidence ? "insufficient" : ""}`}
        onClick={handleClick}
        onKeyDown={handleKeyDown}
        dangerouslySetInnerHTML={{ __html: html ?? "" }}
      />

      {streaming && <span className="stream-caret" aria-hidden="true" />}

      <Sources
        citations={citations}
        focusedLabel={focusedLabel}
        onFocusHandled={() => setFocusedLabel(null)}
      />

      {/* Announce a provider downgrade inline: a local 7B and a frontier cloud
          model produce visibly different quality, and an unexplained switch
          reads as the product being randomly unreliable. */}
      {provider && provider !== "anthropic" && citations.length === 0 && !insufficientEvidence && (
        <div style={{ marginTop: 8, fontSize: 12, color: "var(--text-subtle)" }}>
          No sources were cited for this response.
        </div>
      )}
    </div>
  );
}
