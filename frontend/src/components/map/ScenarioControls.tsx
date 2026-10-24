import type { ChangeEvent } from "react";
import type { SeverityPreset } from "../../api";
import { SEVERITY_STEPS } from "../../lib/scenario";
import "./panel.css";

interface Props {
  severity: SeverityPreset;
  /** True while the map is reloading for a new scenario. */
  updating: boolean;
  onChange: (severity: SeverityPreset) => void;
}

/** Storm strength. It is a scenario the person chooses, because no weather data is used. */
export function ScenarioControls({ severity, updating, onChange }: Props) {
  const index = Math.max(
    0,
    SEVERITY_STEPS.findIndex((step) => step.id === severity),
  );
  const current = SEVERITY_STEPS[index];

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    const next = SEVERITY_STEPS[Number(event.target.value)];
    if (next) onChange(next.id);
  };

  return (
    <div className="scenario">
      <label className="field__label" htmlFor="severity-range">
        Storm strength
      </label>
      <input
        id="severity-range"
        className="scenario__range"
        type="range"
        min={0}
        max={SEVERITY_STEPS.length - 1}
        step={1}
        value={index}
        onChange={handleChange}
        aria-valuetext={current.label}
        aria-describedby="severity-hint"
      />
      <ol className="scenario__ticks" aria-hidden="true">
        {SEVERITY_STEPS.map((step, position) => (
          <li key={step.id} className={position === index ? "is-current" : undefined}>
            {step.label}
          </li>
        ))}
      </ol>
      <p id="severity-hint" className="field__hint">
        <strong>{current.label}.</strong> {current.hint}{" "}
        <span className="scenario__assumption">
          This is a scenario you choose, not a weather forecast.
        </span>
      </p>
      <p className="sr-only" role="status">
        {updating ? "Updating the map for this storm." : ""}
      </p>
    </div>
  );
}
