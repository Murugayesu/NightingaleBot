import { useMemo, useState } from "react";

import { Markdown } from "../../lib/markdown";
import { t } from "../../lib/strings";
import { ModeBadge } from "./ModeBadge";
import { Sources } from "./Sources";
import type { ChatMessage } from "./useChat";

interface MessageViewProps {
  message: ChatMessage;
  isLast: boolean;
  busy: boolean;
  contactUrl: string | undefined;
  canRate: boolean;
  onRegenerate(): void;
  onRate(rating: 1 | -1 | null, comment?: string): Promise<void>;
}

export function MessageView({
  message,
  isLast,
  busy,
  contactUrl,
  canRate,
  onRegenerate,
  onRate,
}: MessageViewProps) {
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);
  const [askComment, setAskComment] = useState(false);
  const [comment, setComment] = useState("");
  const [thanks, setThanks] = useState(false);
  const sources = useMemo(
    () => new Set(message.citations.map((c) => c.source_index)),
    [message.citations],
  );
  // List only the sources the answer actually cites (all of them if it
  // cites none, e.g. an imported answer).
  const cited = useMemo(() => {
    const used = new Set(
      [...message.content.matchAll(/\[Source\s+(\d+)\]/gi)].map((m) => Number(m[1])),
    );
    const list = message.citations.filter((c) => used.has(c.source_index));
    return list.length ? list : message.citations;
  }, [message.citations, message.content]);

  if (message.role === "user") {
    return (
      <div className="message message-user">
        <div className="bubble">{message.content}</div>
      </div>
    );
  }

  const streaming = message.status === "streaming";
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard blocked: nothing to do
    }
  };
  const rate = async (rating: 1 | -1) => {
    const next = message.feedback === rating ? null : rating;
    await onRate(next);
    setThanks(next !== null);
    setAskComment(next === -1);
  };
  const sendComment = async () => {
    await onRate(-1, comment.trim() || undefined);
    setAskComment(false);
    setComment("");
  };
  const showContact =
    !streaming &&
    (message.answerMode === "declined" || message.answerMode === "out_of_scope") &&
    contactUrl;

  return (
    <article className={`message message-assistant${streaming ? " is-streaming" : ""}`}>
      {message.answerMode && <ModeBadge mode={message.answerMode} label={message.label} />}
      <div className="answer">
        {message.content ? (
          <Markdown
            text={message.content}
            sources={sources}
            onCite={(n) => {
              setSourcesOpen(true);
              setHighlighted(n);
            }}
          />
        ) : streaming ? (
          <p className="typing" aria-hidden="true">
            <span />
            <span />
            <span />
          </p>
        ) : null}
      </div>

      {showContact && (
        <p className="contact-line">
          <a href={contactUrl} target="_blank" rel="noopener noreferrer">
            {t.chat.contact} ↗
          </a>
        </p>
      )}
      {message.status === "stopped" && <p className="message-note">{t.chat.stopped}</p>}
      {message.status === "error" && <p className="message-note is-error">{t.chat.failed}</p>}
      {message.isImported && <p className="message-note">{t.chat.imported}</p>}

      {!streaming && (
        <Sources
          citations={cited}
          highlighted={highlighted}
          open={sourcesOpen}
          onToggle={() => setSourcesOpen((open) => !open)}
        />
      )}

      {!streaming && message.content && (
        <div className="message-actions">
          <button type="button" className="icon-button" onClick={copy} title={t.chat.copy}>
            <span aria-hidden="true">⧉</span>
            <span className="sr-only">{t.chat.copy}</span>
            {copied && <span className="action-flash">{t.chat.copied}</span>}
          </button>
          {canRate && message.id && (
            <>
              <button
                type="button"
                className={`icon-button${message.feedback === 1 ? " is-active" : ""}`}
                aria-pressed={message.feedback === 1}
                onClick={() => rate(1)}
                title={t.chat.helpful}
              >
                <span aria-hidden="true">👍</span>
                <span className="sr-only">{t.chat.helpful}</span>
              </button>
              <button
                type="button"
                className={`icon-button${message.feedback === -1 ? " is-active" : ""}`}
                aria-pressed={message.feedback === -1}
                onClick={() => rate(-1)}
                title={t.chat.notHelpful}
              >
                <span aria-hidden="true">👎</span>
                <span className="sr-only">{t.chat.notHelpful}</span>
              </button>
            </>
          )}
          {isLast && !busy && (
            <button
              type="button"
              className="icon-button"
              onClick={onRegenerate}
              title={t.chat.regenerate}
            >
              <span aria-hidden="true">↻</span>
              <span className="sr-only">{t.chat.regenerate}</span>
            </button>
          )}
          {thanks && !askComment && <span className="action-flash">{t.chat.feedbackThanks}</span>}
        </div>
      )}
      {askComment && (
        <form
          className="feedback-form"
          onSubmit={(event) => {
            event.preventDefault();
            void sendComment();
          }}
        >
          <label htmlFor={`fb-${message.key}`}>{t.chat.feedbackPrompt}</label>
          <textarea
            id={`fb-${message.key}`}
            maxLength={1000}
            rows={2}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
          />
          <div className="form-row">
            <button type="submit" className="button button-small">
              {t.common.save}
            </button>
            <button
              type="button"
              className="button button-small button-ghost"
              onClick={() => setAskComment(false)}
            >
              {t.common.cancel}
            </button>
          </div>
        </form>
      )}
    </article>
  );
}
