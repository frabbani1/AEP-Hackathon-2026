import { useState } from "react";
import type { MessageStage, ProactiveMessage } from "../../api";
import { MESSAGES_HELP } from "../../lib/explain";
import { Card } from "./Card";

const STAGE_LABEL: Record<MessageStage, string> = {
  "pre-storm": "Before the storm",
  "outage-confirmed": "Outage confirmed",
  "estimate-updated": "Estimate updated",
  restored: "Power restored",
};

/** The proactive messages for each stage, written in plain language with numbers from the model. */
export function MessagesCard({ messages }: { messages: ProactiveMessage[] }) {
  const [stage, setStage] = useState<MessageStage>("pre-storm");
  const current = messages.find((message) => message.stage === stage) ?? messages[0];
  if (!current) return null;

  return (
    <Card id="messages" title="Messages we would send" info={MESSAGES_HELP}>
      <fieldset className="segmented">
        <legend className="sr-only">Message stage</legend>
        {messages.map((message) => (
          <label key={message.stage} className="segmented__option">
            <input
              type="radio"
              name="message-stage"
              value={message.stage}
              checked={current.stage === message.stage}
              onChange={() => setStage(message.stage)}
            />
            <span>{STAGE_LABEL[message.stage]}</span>
          </label>
        ))}
      </fieldset>

      <figure className="message">
        <figcaption className="message__meta">
          <strong>{current.channel}</strong>
          {current.subject && <span> &middot; Subject: {current.subject}</span>}
        </figcaption>
        <div className="message__bubble">
          {current.body.split("\n\n").map((paragraph, index) => (
            <p key={index}>{paragraph}</p>
          ))}
        </div>
      </figure>
      <p className="card__muted">
        {current.origin === "template"
          ? "Fixed wording with numbers filled in from the model. Nothing here is written freehand."
          : "Written by an AI assistant using only numbers from the model."}
      </p>
    </Card>
  );
}
