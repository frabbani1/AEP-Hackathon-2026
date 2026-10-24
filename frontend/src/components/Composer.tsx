import { useEffect, useState, type FormEvent, type KeyboardEvent, type RefObject } from "react";
import { ArrowUpIcon } from "./icons";
import "./Composer.css";

const MAX_LENGTH = 1000;
const MAX_HEIGHT_PX = 200;

interface Props {
  location: string | null;
  pending: boolean;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  onSend: (text: string) => void;
}

export function Composer({ location, pending, inputRef, onSend }: Props) {
  const [value, setValue] = useState("");
  const canSend = value.trim().length > 0 && !pending;

  // Grow the textarea with its content, up to a cap.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [value, inputRef]);

  const submit = () => {
    if (!canSend) return;
    onSend(value);
    setValue("");
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    submit();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends, Shift+Enter adds a line. Ignore Enter while an IME is composing.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="composer">
      <div className="container composer__inner">
        <form className="composer__form" onSubmit={handleSubmit}>
          <label htmlFor="chat-input" className="sr-only">
            Message the Outage Concierge
          </label>
          <div className="composer__box">
            <textarea
              ref={inputRef}
              id="chat-input"
              className="composer__input"
              rows={1}
              maxLength={MAX_LENGTH}
              placeholder={
                location
                  ? `Ask about outages in ${location}…`
                  : "Ask about outages, restoration, or how to prepare…"
              }
              value={value}
              onChange={(event) => setValue(event.target.value)}
              onKeyDown={handleKeyDown}
              aria-describedby="composer-note"
            />
            <button type="submit" className="btn btn-danger composer__send" disabled={!canSend}>
              <ArrowUpIcon size={22} />
              <span className="sr-only">Send message</span>
            </button>
          </div>
          <p id="composer-note" className="composer__note">
            Answers are model estimates, not confirmed restoration times. Don&rsquo;t share street
            addresses or account numbers.
          </p>
        </form>
      </div>
    </div>
  );
}
