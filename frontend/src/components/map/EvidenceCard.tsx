import type { EvidenceVerdict } from "../../api";
import type { EvidenceState } from "../../hooks/useEvidence";
import { EVIDENCE_HELP } from "../../lib/explain";
import { Card, Tag } from "./Card";

const LEVEL_LABEL: Record<EvidenceVerdict["level"], string> = {
  supports: "Held up",
  mixed: "Mixed",
  does_not_support: "Did not hold up",
  not_testable: "Not testable",
};

/** How the model did on past storms, in the backtest's own words. Aggregates only; it never names a place. */
export function EvidenceCard({ state }: { state: EvidenceState }) {
  if (state.status === "loading") return null;
  if (state.status === "error") {
    return (
      <Card id="evidence" title="How well has this held up?" info={EVIDENCE_HELP}>
        <p className="card__muted">We couldn&rsquo;t load the backtest results just now.</p>
      </Card>
    );
  }
  const { data } = state;
  return (
    <Card id="evidence" title="How well has this held up?" info={EVIDENCE_HELP} tag={<Tag kind="model" />}>
      {data.status === "ok" ? (
        <>
          {data.label && <p>{data.label}</p>}
          <ul className="verdicts">
            {data.verdicts.map((verdict) => (
              <li key={verdict.code} className="verdicts__item">
                <span className={`verdicts__level verdicts__level--${verdict.level}`}>{LEVEL_LABEL[verdict.level]}</span>
                <span>{verdict.sentence}</span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p>The backtest results have not been published yet.</p>
      )}
      <h4 className="card__subhead">Limits</h4>
      <ul className="plain">
        {data.limits.map((limit) => (
          <li key={limit}>{limit}</li>
        ))}
      </ul>
      <p className="card__muted">{data.note}</p>
    </Card>
  );
}
