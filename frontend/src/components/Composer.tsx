/**
 * Message composer.
 *
 * The skill buttons are an explicit override of the backend's router. The
 * router infers intent from phrasing and gets it right most of the time, but
 * "make it shorter" is genuinely ambiguous — so when the user states intent,
 * we send it and skip the guess entirely.
 */

import { useEffect, useRef, useState } from "react";

interface Props {
  disabled: boolean;
  streaming: boolean;
  hasConversation: boolean;
  onSend: (message: string, skill: string | null) => void;
  onStop: () => void;
}

const SKILLS = [
  { id: "write_ship30_essay", label: "Write essay", needsContext: false },
  { id: "create_artifact", label: "Make document", needsContext: false },
] as const;

export default function Composer({
  disabled,
  streaming,
  hasConversation,
  onSend,
  onStop,
}: Props) {
  const [value, setValue] = useState("");
  const [skill, setSkill] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Grow the textarea with its content, up to the CSS max-height.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 190)}px`;
  }, [value]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed, skill);
    setValue("");
    setSkill(null);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends, Shift+Enter newlines — the convention users already expect
    // from every chat interface, so it needs no explanation.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="composer-wrap">
      <div className="composer">
        <div className="composer-actions">
          {SKILLS.map((item) => (
            <button
              key={item.id}
              className="skill-btn"
              aria-pressed={skill === item.id}
              disabled={disabled}
              onClick={() => setSkill((current) => (current === item.id ? null : item.id))}
              title={
                hasConversation
                  ? "Builds on the sources from your last answer"
                  : "Searches the transcripts for this topic"
              }
            >
              {item.label}
            </button>
          ))}
          {skill && (
            <button className="skill-btn" onClick={() => setSkill(null)}>
              Clear
            </button>
          )}
        </div>

        <div className="composer-input">
          <label htmlFor="composer-textarea" className="sr-only">
            Ask a product or growth question
          </label>
          <textarea
            id="composer-textarea"
            ref={textareaRef}
            rows={1}
            value={value}
            placeholder={
              skill === "write_ship30_essay"
                ? "What should the essay be about?"
                : skill === "create_artifact"
                  ? "What document should I build?"
                  : "Ask about product, growth, hiring, metrics…"
            }
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled}
          />

          {streaming ? (
            <button className="btn-send btn-stop" onClick={onStop} title="Stop generating">
              ■
            </button>
          ) : (
            <button
              className="btn-send"
              onClick={submit}
              disabled={disabled || !value.trim()}
              title="Send"
            >
              ↑
            </button>
          )}
        </div>

        <div className="composer-hint">
          <span>
            <kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> for a new line
          </span>
          <span>Answers are grounded in podcast transcripts only</span>
        </div>
      </div>
    </div>
  );
}
