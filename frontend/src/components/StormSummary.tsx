import { useState, type ReactNode } from "react";
import type { EventKind } from "../api";
import type { SummaryState } from "../hooks/useEventSummary";
import { BoltIcon, ChevronIcon, ClockIcon, StormIcon, WarningIcon } from "./icons";
import "./StormSummary.css";

const ICONS: Record<EventKind, ReactNode> = {
  storm: <StormIcon size={20} />,
  outage: <BoltIcon size={20} />,
  restoration: <ClockIcon size={20} />,
  safety: <WarningIcon size={20} />,
};

export function StormSummary({ state }: { state: SummaryState }) {
  // Start collapsed on small screens so the conversation keeps the room.
  const [open, setOpen] = useState(() => window.matchMedia("(min-width: 768px)").matches);

  return (
    <section className="summary" aria-labelledby="summary-title">
      <div className="container">
        <div className="summary__head">
          <h2 id="summary-title" className="summary__title">
            Storm summary
          </h2>
          {state.status === "ready" && state.summary.source === "sample" && (
            <span className="summary__badge">Sample data</span>
          )}
          <button
            type="button"
            className="summary__toggle"
            aria-expanded={open}
            aria-controls="summary-body"
            onClick={() => setOpen((value) => !value)}
          >
            {open ? "Hide" : "Show"}
            <span className="sr-only"> storm summary</span>
            <ChevronIcon size={18} className={open ? "summary__chevron is-open" : "summary__chevron"} />
          </button>
        </div>

        <div id="summary-body" className="summary__body" hidden={!open} aria-busy={state.status === "loading"}>
          {state.status === "loading" && <p className="summary__status">Loading the latest storm summary…</p>}

          {state.status === "error" && (
            <p className="summary__status">
              We couldn&rsquo;t load the storm summary. You can still ask a question below.
            </p>
          )}

          {state.status === "ready" && (
            <>
              <p className="summary__headline">{state.summary.headline}</p>
              <ul className="summary__events">
                {state.summary.events.map((event) => (
                  <li key={event.id} className={`summary__event summary__event--${event.kind}`}>
                    <span className="summary__icon">{ICONS[event.kind]}</span>
                    <span className="summary__text">
                      <span className="summary__event-title">{event.title}</span>
                      <span className="summary__event-detail">{event.detail}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
