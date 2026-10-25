import type { ConfidenceLevel, RiskAssessment, SeverityInfo } from "../../api";
import { BAND_SUMMARY } from "../../lib/bands";
import { CONFIDENCE_HELP, FACTORS_HELP, RISK_HELP } from "../../lib/explain";
import { InfoTip } from "../InfoTip";
import { ArrowDownIcon, ArrowUpIcon } from "../icons";
import { Card, RiskBadge, SubHead, Tag } from "./Card";

const LEVEL_BARS: Record<ConfidenceLevel, number> = { Low: 1, Medium: 2, High: 3 };

function ConfidenceMeter({ level }: { level: ConfidenceLevel }) {
  return (
    <span className="meter" aria-hidden="true">
      {[1, 2, 3].map((position) => (
        <span key={position} className={position <= LEVEL_BARS[level] ? "is-on" : undefined} />
      ))}
    </span>
  );
}

/** "Will I lose power?" Band, confidence, and the reasons behind them. */
export function RiskCard({ risk, severity }: { risk: RiskAssessment; severity: SeverityInfo }) {
  return (
    <Card id="risk" title="Will I lose power?" info={RISK_HELP} tag={<Tag kind="model" />}>
      <div className="risk">
        <RiskBadge band={risk.band} size="lg" />
        <p className="risk__summary">{BAND_SUMMARY[risk.band]}</p>
      </div>
      <p className="card__muted">{risk.scopeNote}</p>

      <p className="confidence">
        <strong>Confidence: {risk.confidence.level}</strong>
        <ConfidenceMeter level={risk.confidence.level} />
        <InfoTip label="confidence">{CONFIDENCE_HELP}</InfoTip>
      </p>
      <p className="card__muted">
        {risk.confidence.reason}
        {severity.extrapolated && " Confidence is lowered because this storm is stronger than any in our records."}
      </p>

      {risk.explanation && (
        <>
          <h4 className="card__subhead">In plain words</h4>
          <p className="explanation">{risk.explanation.text}</p>
          <p className="card__muted">
            {risk.explanation.origin === "model"
              ? "Written by an AI assistant using only numbers from the model."
              : "Fixed wording with numbers filled in from the model."}
          </p>
        </>
      )}
      <SubHead info={FACTORS_HELP}>Why we say this</SubHead>
      <ul className="factors">
        {risk.topFactors.map((factor) => (
          <li key={factor.id} className={`factors__item factors__item--${factor.direction}`}>
            <span className="factors__icon" aria-hidden="true">
              {factor.direction === "raises" ? <ArrowUpIcon size={18} /> : <ArrowDownIcon size={18} />}
            </span>
            <span className="factors__body">
              <span className="factors__dir">{factor.direction === "raises" ? "Raises risk" : "Lowers risk"}</span>
              <span className="factors__text">{factor.text}</span>
              <span className="factors__bar" aria-hidden="true">
                <span style={{ width: `${Math.round(factor.weight * 100)}%` }} />
              </span>
            </span>
          </li>
        ))}
      </ul>

      {risk.bandEvidence && <p className="card__muted">{risk.bandEvidence}</p>}
    </Card>
  );
}
