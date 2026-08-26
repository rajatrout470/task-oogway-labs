/**
 * Source panel — the evidence behind an answer.
 *
 * Collapsed by default so the answer stays the focus, but the count is always
 * visible: "6 sources" is itself a trust signal, and hiding it entirely would
 * bury the thing that distinguishes this product from a generic chatbot.
 *
 * Each source deep-links to the exact second of the source video. Excerpts are
 * short and always attributed — they point back to the original rather than
 * standing in for it.
 */

import { useEffect, useRef, useState } from "react";
import type { Citation } from "../lib/types";

interface Props {
  citations: Citation[];
  /** Set when a citation chip is clicked, to scroll to and flash that source. */
  focusedLabel: string | null;
  onFocusHandled: () => void;
}

export default function Sources({ citations, focusedLabel, onFocusHandled }: Props) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Clicking a chip expands the panel and scrolls to the matching source.
  useEffect(() => {
    if (!focusedLabel) return;
    setOpen(true);

    // Wait a frame so the panel has expanded before measuring scroll position.
    const raf = requestAnimationFrame(() => {
      const target = containerRef.current?.querySelector(
        `[data-source-label="${focusedLabel}"]`,
      );
      target?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      onFocusHandled();
    });
    return () => cancelAnimationFrame(raf);
  }, [focusedLabel, onFocusHandled]);

  if (!citations.length) return null;

  const episodes = new Set(citations.map((c) => c.episode_slug)).size;

  return (
    <div className="sources" ref={containerRef}>
      <button
        className="sources-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="sources-count">{citations.length}</span>
        <span>
          {citations.length === 1 ? "source" : "sources"} from {episodes}{" "}
          {episodes === 1 ? "episode" : "episodes"}
        </span>
        <span className={`sources-chevron ${open ? "open" : ""}`} aria-hidden="true">
          ›
        </span>
      </button>

      {open &&
        citations.map((citation) => (
          <div
            key={citation.label}
            className={`source-item ${focusedLabel === citation.label ? "highlight" : ""}`}
            data-source-label={citation.label}
          >
            <div className="source-head">
              <span className="source-label">{citation.label}</span>
              <span className="source-guest">{citation.guest}</span>
              <span className="source-episode">{citation.episode_title}</span>
            </div>

            {citation.quote && <div className="source-quote">{citation.quote}</div>}

            {citation.source_url && (
              <a
                className="source-link"
                href={citation.source_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                ▶ Watch
                {/* No timestamp means the source transcript genuinely had none —
                    we link to the episode rather than invent a position. */}
                {citation.timestamp ? ` at ${citation.timestamp}` : " (episode)"}
              </a>
            )}
          </div>
        ))}
    </div>
  );
}
