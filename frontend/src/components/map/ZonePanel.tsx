import type { DeclaredNeed, Scenario, ZoneAssessment } from "../../api";
import type { AssessmentState } from "../../hooks/useZoneAssessment";
import { BoltIcon, ClockIcon, ChecklistIcon, BellIcon, InfoIcon, PhoneIcon } from "../icons";
import { AlertsCard } from "./AlertsCard";
import { Card, Tag } from "./Card";
import { MessagesCard } from "./MessagesCard";
import { PrepareCard } from "./PrepareCard";
import { RestorationCard } from "./RestorationCard";
import { RiskCard } from "./RiskCard";
import { TransparencyCard } from "./TransparencyCard";

interface Props {
  state: AssessmentState;
  scenario: Scenario;
  minHomesPerArea: number | null;
  needs: DeclaredNeed[];
  onNeedsChange: (needs: DeclaredNeed[]) => void;
  onScenarioChange: (patch: Partial<Scenario>) => void;
  onResetWhatIf: () => void;
  onRetry: () => void;
}

const QUESTIONS = [
  { text: "Will I lose power?", icon: <BoltIcon size={22} /> },
  { text: "How long could restoration take?", icon: <ClockIcon size={22} /> },
  { text: "What should I do to prepare?", icon: <ChecklistIcon size={22} /> },
  { text: "Do I qualify for outage alerts?", icon: <BellIcon size={22} /> },
];

function EmptyState() {
  return (
    <div className="panel-empty">
      <h2 className="panel-empty__title">Pick an area to get answers</h2>
      <p>Choose an area on the map or from the list. You will see:</p>
      <ul className="panel-empty__list">
        {QUESTIONS.map((question) => (
          <li key={question.text}>
            {question.icon}
            <span>{question.text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function summaryLine(data: ZoneAssessment): string {
  if (data.status !== "ok" || !data.risk || !data.restoration) {
    return `${data.zoneName}: not enough data to rate this area. ${data.fallback?.message ?? ""}`;
  }
  return `${data.zoneName}: ${data.risk.band} outage risk, confidence ${data.risk.confidence.level}. If an outage happens, restoration is estimated at ${data.restoration.rangeText}.`;
}

/** Everything about the chosen area, in the order of the customer's four questions. */
export function ZonePanel({
  state,
  scenario,
  minHomesPerArea,
  needs,
  onNeedsChange,
  onScenarioChange,
  onResetWhatIf,
  onRetry,
}: Props) {
  if (state.status === "idle") return <EmptyState />;

  if (state.status === "loading") {
    return (
      <div className="panel-status" role="status" aria-busy="true">
        <p>Loading this area&hellip;</p>
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="panel-status panel-status--error" role="alert">
        <p>
          <strong>We couldn&rsquo;t load this area.</strong> Please try again. If this is an emergency, such as a
          downed line, call 911.
        </p>
        <button type="button" className="btn btn-outline-danger" onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  }

  const { data, refreshing } = state;

  return (
    <div className="zone-panel" aria-busy={refreshing}>
      {/* Announces the new answer after every change, without moving focus. */}
      <p className="sr-only" role="status">
        {refreshing ? "Updating." : summaryLine(data)}
      </p>

      <div className="zone-panel__head">
        <h2 className="zone-panel__title">{data.zoneName}</h2>
        {data.source === "sample" && <Tag kind="sample" />}
        {refreshing && (
          <span className="zone-panel__updating" aria-hidden="true">
            Updating&hellip;
          </span>
        )}
      </div>
      {data.criticalFacilityNearby && (
        <p className="callout callout--plain">
          <InfoIcon size={20} />
          <span>
            A critical facility, such as a hospital, fire station, or ambulance service, is nearby. This can move
            alerts earlier. We never say which one or where.
          </span>
        </p>
      )}

      <section className="confirmed" aria-labelledby="confirmed-title">
        <div className="card__head">
          <h3 id="confirmed-title" className="card__title">
            Confirmed information
          </h3>
          <Tag kind="confirmed" />
        </div>
        <p>{data.confirmed.note}</p>
      </section>

      {data.fallback && (
        <section className="fallback" aria-labelledby="fallback-title">
          <h3 id="fallback-title" className="card__title">
            We can&rsquo;t rate this area reliably
          </h3>
          <p>{data.fallback.message}</p>
          <p>The safety steps and alert options below still apply. A person can also help.</p>
          <a className="btn btn-outline-danger" href="tel:8006722231">
            <PhoneIcon size={20} />
            Call 800-672-2231
          </a>
        </section>
      )}

      {data.risk && <RiskCard risk={data.risk} severity={data.severity} />}

      {data.restoration && (
        <RestorationCard
          restoration={data.restoration}
          scenario={scenario}
          onScenarioChange={onScenarioChange}
          onReset={onResetWhatIf}
        />
      )}

      <PrepareCard preparation={data.preparation} needs={needs} onNeedsChange={onNeedsChange} />
      <AlertsCard alerts={data.alerts} />

      {data.messages.length > 0 ? (
        <MessagesCard messages={data.messages} />
      ) : (
        <Card id="messages" title="Messages we would send">
          <p className="card__muted">
            Without a reliable rating we don&rsquo;t write storm-specific messages for this area. Alerts, if you
            turn them on, would only say when an outage is confirmed and when power is back.
          </p>
        </Card>
      )}

      <TransparencyCard transparency={data.transparency} minHomesPerArea={minHomesPerArea} />
    </div>
  );
}
