import {
  type FormEvent,
  type KeyboardEvent,
  forwardRef,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { t } from "../../lib/strings";

export interface ComposerHandle {
  focus(): void;
  setText(text: string): void;
}

interface ComposerProps {
  busy: boolean;
  maxChars: number;
  disabled?: boolean;
  onSend(text: string): Promise<boolean>;
  onStop(): void;
}

/** The question box: Enter sends, Shift+Enter adds a line, Stop while answering. */
export const Composer = forwardRef<ComposerHandle, ComposerProps>(function Composer(
  { busy, maxChars, disabled = false, onSend, onStop },
  ref,
) {
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useImperativeHandle(ref, () => ({
    focus: () => inputRef.current?.focus(),
    setText: (value: string) => setText(value),
  }));

  // Grow with the text up to a limit, then scroll.
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 240)}px`;
  }, [text]);

  const tooLong = text.length > maxChars;
  const canSend = !busy && !disabled && text.trim().length > 0 && !tooLong;

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!canSend) return;
    const question = text;
    setText("");
    const accepted = await onSend(question);
    if (!accepted) setText(question); // rejected (quota, network): give it back
    inputRef.current?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  };

  return (
    <form className="composer" onSubmit={submit}>
      <label htmlFor="composer-input" className="sr-only">
        {t.chat.placeholder}
      </label>
      <textarea
        id="composer-input"
        ref={inputRef}
        rows={1}
        value={text}
        placeholder={t.chat.placeholder}
        disabled={disabled}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        aria-describedby="composer-help"
        aria-invalid={tooLong}
      />
      {busy ? (
        <button type="button" className="send-button is-stop" onClick={onStop} title={t.chat.stop}>
          <span aria-hidden="true">■</span>
          <span className="sr-only">{t.chat.stop}</span>
        </button>
      ) : (
        <button type="submit" className="send-button" disabled={!canSend} title={t.chat.send}>
          <span aria-hidden="true">↑</span>
          <span className="sr-only">{t.chat.send}</span>
        </button>
      )}
      <p id="composer-help" className={`composer-help${tooLong ? " is-error" : ""}`}>
        {tooLong ? t.chat.tooLong(maxChars) : t.chat.shortcuts}
      </p>
    </form>
  );
});
