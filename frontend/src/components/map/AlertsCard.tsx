import type { AlertEligibility } from "../../api";
import { CheckIcon, InfoIcon } from "../icons";
import { Card, Tag } from "./Card";

/** "Do I qualify for outage alerts?" The rules that fired, plus timing, channels and escalation. */
export function AlertsCard({ alerts }: { alerts: AlertEligibility }) {
  return (
    <Card id="alerts" title="Do I qualify for outage alerts?" tag={<Tag kind="rules" />}>
      <p className={alerts.eligible ? "verdict verdict--yes" : "verdict verdict--no"}>
        {alerts.eligible ? <CheckIcon size={22} /> : <InfoIcon size={22} />}
        <strong>{alerts.eligible ? "Yes, you qualify" : "Not triggered right now"}</strong>
      </p>
      <p>{alerts.summary}</p>

      <h4 className="card__subhead">The rules we checked</h4>
      <ul className="rules">
        {alerts.rules.map((rule) => (
          <li key={rule.id} className={rule.met ? "rules__item is-met" : "rules__item"}>
            <span className="rules__mark" aria-hidden="true">
              {rule.met ? <CheckIcon size={16} /> : null}
            </span>
            <span>
              <span className="sr-only">{rule.met ? "Met: " : "Not met: "}</span>
              {rule.text}
            </span>
          </li>
        ))}
      </ul>

      {alerts.eligible && (
        <>
          <h4 className="card__subhead">When and how we would reach you</h4>
          <dl className="facts">
            <dt>Timing</dt>
            <dd>{alerts.timing}</dd>
            <dt>Channels</dt>
            <dd>
              <ul className="chips">
                {alerts.channels.map((channel) => (
                  <li key={channel} className="chip">
                    {channel}
                  </li>
                ))}
              </ul>
            </dd>
          </dl>

          {alerts.escalation.length > 0 && (
            <>
              <h4 className="card__subhead">If things get worse</h4>
              <ul className="plain">
                {alerts.escalation.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ul>
            </>
          )}
        </>
      )}

      {alerts.note && <p className="card__muted">{alerts.note}</p>}
    </Card>
  );
}
