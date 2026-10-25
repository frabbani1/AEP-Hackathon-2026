import type { ReactNode } from "react";
import type { ChatMessage } from "../api";
import { TAG_HELP } from "../lib/explain";
import { InfoTip } from "./InfoTip";
import { BellIcon, BoltIcon, ChecklistIcon, ClockIcon } from "./icons";
import "./ChatThread.css";

interface Prompt {
  text: string;
  tone: "orange" | "teal" | "blue" | "green";
  icon: ReactNode;
}

// The four questions from the challenge brief.
const PROMPTS: Prompt[] = [
  { text: "Will I lose power?", tone: "orange", icon: <BoltIcon size={26} /> },
  { text: "How long could restoration take?", tone: "teal", icon: <ClockIcon size={26} /> },
  { text: "What should I do to prepare?", tone: "blue", icon: <ChecklistIcon size={26} /> },
  { text: "Do I qualify for outage alerts?", tone: "green", icon: <BellIcon size={26} /> },
];

interface Props {
  messages: ChatMessage[];
  pending: boolean;
  onPrompt: (question: string) => void;
}

/**
 * Blocks are separated by a blank line. In a block, lines that start with "- " become a list, and any other lines
 * before them become the lead-in text. Text is always rendered as text, never as markup.
 */
function Paragraphs({ text }: { text: string }) {
  return text.split("\n\n").map((block, index) => {
    const lines = block.split("\n");
    const items = lines.filter((line) => line.startsWith("- "));
    if (items.length === 0) return <p key={index}>{block}</p>;
    const lead = lines.filter((line) => !line.startsWith("- ")).join(" ");
    return (
      <div key={index}>
        {lead && <p>{lead}</p>}
        <ul className="msg__list">
          {items.map((item) => (
            <li key={item}>{item.slice(2)}</li>
          ))}
        </ul>
      </div>
    );
  });
}

function Message({ message }: { message: ChatMessage }) {
  if (message.role === "notice") {
    return (
      <li className="msg msg--notice">
        <span>{message.content}</span>
      </li>
    );
  }

  if (message.role === "user") {
    return (
      <li className="msg msg--user">
        <div className="msg__bubble">
          <span className="sr-only">You said: </span>
          <Paragraphs text={message.content} />
        </div>
      </li>
    );
  }

  return (
    <li className="msg msg--assistant">
      <span className="msg__avatar" aria-hidden="true">
        <BoltIcon size={18} />
      </span>
      <div
        className={
          message.isError ? "msg__body msg__body--error" : message.isForecast ? "msg__body msg__body--forecast" : "msg__body"
        }
      >
        <p className="msg__author">
          <span className="sr-only">Outage Concierge said: </span>
          <span aria-hidden="true">Outage Concierge</span>
          {message.isEstimate && (
            <>
              <span className="msg__tag">Model estimate &middot; not confirmed</span>
              <InfoTip label="model estimates">{TAG_HELP.model}</InfoTip>
            </>
          )}
          {message.isForecast && (
            <>
              <span className="msg__tag msg__tag--forecast">Prediction only &middot; no outage data</span>
              <InfoTip label="prediction only">{TAG_HELP.forecast}</InfoTip>
            </>
          )}
        </p>
        <Paragraphs text={message.content} />
      </div>
    </li>
  );
}

export function ChatThread({ messages, pending, onPrompt }: Props) {
  // Location notices alone don't count: the welcome prompts stay until the first question.
  const hasMessages = messages.some((message) => message.role !== "notice");

  return (
    <div className="thread">
      <div className="container thread__inner">
        {/* Single page h1: a visible welcome title first, then screen-reader only. */}
        <h1 className={hasMessages ? "sr-only" : "welcome__title"}>Outage Concierge</h1>

        {!hasMessages && (
          <div className="welcome">
            <p className="welcome__lede">
              Get clear answers before, during, and after a storm. Tell me your city or ZIP code
              (here or in the location box), then ask a question or pick one below.
            </p>
            <ul className="prompts">
              {PROMPTS.map((prompt) => (
                <li key={prompt.text}>
                  <button
                    type="button"
                    className={`prompt prompt--${prompt.tone}`}
                    onClick={() => onPrompt(prompt.text)}
                    disabled={pending}
                  >
                    <span className="prompt__icon">{prompt.icon}</span>
                    <span className="prompt__text">{prompt.text}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        <ol className="messages" role="log" aria-label="Conversation" aria-live="polite" aria-relevant="additions">
          {messages.map((message) => (
            <Message key={message.id} message={message} />
          ))}
          {pending && (
            <li className="msg msg--assistant">
              <span className="msg__avatar" aria-hidden="true">
                <BoltIcon size={18} />
              </span>
              <div className="msg__body">
                <span className="sr-only">Outage Concierge is working on an answer.</span>
                <span className="typing" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
              </div>
            </li>
          )}
        </ol>
      </div>
    </div>
  );
}
