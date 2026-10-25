import type { DamageKind, RestorationEstimate, Scenario } from "../../api";
import {
  ACCESS_OPTIONS,
  CREWS_MAX,
  CREWS_MIN,
  DAMAGE_COUNT_MAX,
  DAMAGE_COUNT_MIN,
  DAMAGE_LABELS,
  DAMAGE_MAX,
  DAMAGE_ORDER,
  DEFAULT_SCENARIO,
} from "../../lib/scenario";
import { ArrowDownIcon, ArrowUpIcon, InfoIcon } from "../icons";
import { Card, Tag } from "./Card";
import { Stepper } from "./Stepper";

/** Hours at the right edge of the range bar. Longer ranges run off the end. */
const BAR_MAX_HOURS = 72;

interface Props {
  restoration: RestorationEstimate;
  scenario: Scenario;
  onScenarioChange: (patch: Partial<Scenario>) => void;
  onReset: () => void;
}

function RangeBar({ low, high }: { low: number; high: number }) {
  const start = Math.min(low, BAR_MAX_HOURS);
  const end = Math.min(high, BAR_MAX_HOURS);
  return (
    <div className="rangebar">
      <div
        className="rangebar__track"
        role="img"
        aria-label={`Estimated range: ${low} to ${high} hours, on a scale from 0 to ${BAR_MAX_HOURS} hours or more.`}
      >
        <span
          className={high > BAR_MAX_HOURS ? "rangebar__fill is-open" : "rangebar__fill"}
          style={{ left: `${(start / BAR_MAX_HOURS) * 100}%`, width: `${((end - start) / BAR_MAX_HOURS) * 100}%` }}
        />
      </div>
      <div className="rangebar__scale" aria-hidden="true">
        <span>0</span>
        <span>24 h</span>
        <span>48 h</span>
        <span>{BAR_MAX_HOURS} h+</span>
      </div>
    </div>
  );
}

const MOVER_ICON = {
  longer: <ArrowUpIcon size={18} />,
  shorter: <ArrowDownIcon size={18} />,
  either: <InfoIcon size={18} />,
} as const;

const MOVER_LABEL = {
  longer: "Adds time",
  shorter: "Saves time",
  either: "Unknown",
} as const;

/** "How long could restoration take?" A range, what moves it, and what-ifs a judge can change. */
export function RestorationCard({ restoration, scenario, onScenarioChange, onReset }: Props) {
  const isDefault =
    scenario.crews === DEFAULT_SCENARIO.crews &&
    scenario.access === DEFAULT_SCENARIO.access &&
    scenario.damageCount === null &&
    scenario.damage === null;

  // The total starts from what the model assumed for this area and storm.
  const assumedCount = restoration.damageCount ?? DAMAGE_COUNT_MIN;
  const chosenKinds = scenario.damage !== null;

  // Choosing kinds of damage replaces the total. Taking every kind back to zero hands control back to the model.
  const setDamage = (kind: DamageKind, value: number) => {
    const next = { pole: 0, fuse: 0, primary: 0, transformer: 0, secondary: 0, ...scenario.damage, [kind]: value };
    const any = DAMAGE_ORDER.some((k) => next[k] > 0);
    onScenarioChange({ damage: any ? next : null });
  };
  return (
    <Card id="restoration" title="How long could restoration take?" tag={<Tag kind="model" />}>
      <p className="card__lead">If an outage happens here, power could take</p>
      <p className="range__text">{restoration.rangeText}</p>
      <RangeBar low={restoration.p50Hours} high={restoration.p90Hours} />
      <p className="card__muted">{restoration.meaning}</p>
      <p className="card__muted">
        Confidence: {restoration.confidence}. This is a model estimate, not a confirmed restoration time.
      </p>

      {restoration.steps.length > 0 && (
        <>
          <h4 className="card__subhead">How power comes back</h4>
          <ol className="plain">
            {restoration.steps.map((step) => (
              <li key={step.stage}>
                {step.label}: likely by about {step.doneByHours} {step.doneByHours === 1 ? "hour" : "hours"}.
              </li>
            ))}
          </ol>
        </>
      )}

      <h4 className="card__subhead">What could move it</h4>
      <ul className="factors">
        {restoration.movers.map((mover) => (
          <li key={mover.id} className={`factors__item factors__item--${mover.direction}`}>
            <span className="factors__icon" aria-hidden="true">
              {MOVER_ICON[mover.direction]}
            </span>
            <span className="factors__body">
              <span className="factors__dir">{MOVER_LABEL[mover.direction]}</span>
              <span className="factors__text">{mover.text}</span>
            </span>
          </li>
        ))}
      </ul>

      <p className="callout">
        <InfoIcon size={20} />
        <span>
          Power often comes back in steps. Repairs closer to the power source restore the most homes first, so some
          homes get power before the end of the range.
        </span>
      </p>

      <fieldset className="whatif">
        <legend className="whatif__legend">Try a what-if</legend>
        <p className="field__hint">Change anything below and the estimate updates.</p>

        <Stepper
          label="Crews working on this area"
          unit="crews"
          value={scenario.crews}
          min={CREWS_MIN}
          max={CREWS_MAX}
          onChange={(crews) => onScenarioChange({ crews })}
        />

        <fieldset className="choice">
          <legend className="choice__legend">Access to the damage</legend>
          {ACCESS_OPTIONS.map(({ value, label }) => (
            <label key={value} className="choice__option">
              <input
                type="radio"
                name="access"
                value={value}
                checked={scenario.access === value}
                onChange={() => onScenarioChange({ access: value })}
              />
              <span>{label}</span>
            </label>
          ))}
        </fieldset>

        <div className="whatif__damage">
          <p className="choice__legend">Repairs to assume</p>
          <p className="field__hint">
            {chosenKinds
              ? "Using the kinds of damage you chose below."
              : scenario.damageCount !== null
                ? "Using your number."
                : "Starts from the model's assumption for this area and storm."}
          </p>
          {!chosenKinds && (
            <Stepper
              label="Repair jobs in total"
              unit="repair jobs"
              value={scenario.damageCount ?? assumedCount}
              min={DAMAGE_COUNT_MIN}
              max={DAMAGE_COUNT_MAX}
              onChange={(damageCount) => onScenarioChange({ damageCount })}
            />
          )}
          <details className="whatif__kinds" open={chosenKinds}>
            <summary>Or choose the kinds of damage</summary>
            <p className="field__hint">
              Only the damage you enter is counted, and it is placed on the line serving a typical home in this area.
            </p>
            {DAMAGE_ORDER.map((kind) => (
              <Stepper
                key={kind}
                label={DAMAGE_LABELS[kind]}
                unit={DAMAGE_LABELS[kind].toLowerCase()}
                value={scenario.damage?.[kind] ?? 0}
                min={0}
                max={DAMAGE_MAX}
                onChange={(value) => setDamage(kind, value)}
              />
            ))}
          </details>
        </div>

        <button type="button" className="btn btn-outline-danger whatif__reset" onClick={onReset} disabled={isDefault}>
          Reset what-ifs
        </button>
      </fieldset>
    </Card>
  );
}
