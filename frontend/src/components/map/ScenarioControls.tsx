import { useRef, type ChangeEvent } from "react";
import type { ScenarioPreset, SeverityPreset, WeatherSummary } from "../../api";
import { STORM_STRENGTH_HELP } from "../../lib/explain";
import { SEVERITY_STEPS } from "../../lib/scenario";
import { InfoTip } from "../InfoTip";
import { CloudIcon } from "../icons";
import "./panel.css";

interface Props {
  severity: SeverityPreset;
  /** True while the map is reloading for a new scenario. */
  updating: boolean;
  /** Map-wide weather summary, when the map was loaded in weather mode. */
  weather: WeatherSummary | null;
  onChange: (severity: SeverityPreset) => void;
}

const DEFAULT_PRESET: ScenarioPreset = "typical";

function gustRange(weather: WeatherSummary): string | null {
  const { peakGustMphMin: lo, peakGustMphMax: hi } = weather;
  if (lo === undefined || hi === undefined) return null;
  return Math.round(lo) === Math.round(hi) ? `${Math.round(hi)} mph` : `${Math.round(lo)} to ${Math.round(hi)} mph`;
}

/** Live weather: what the forecast says, and how it was turned into a storm strength. */
function WeatherStatus({ weather, updating }: { weather: WeatherSummary | null; updating: boolean }) {
  if (!weather) {
    return (
      <p className="field__hint" role="status">
        {updating ? "Checking the forecast for every area\u2026" : "Loading the forecast\u2026"}
      </p>
    );
  }
  if (weather.status !== "ok") {
    return (
      <p className="callout callout--warn" role="status">
        <span>
          <strong>Live weather isn&rsquo;t available right now.</strong>{" "}
          {weather.notice ?? "The map shows a typical storm instead."} Try again in a few minutes, or switch to
          &ldquo;What-if storm&rdquo; to explore a scenario.
        </span>
      </p>
    );
  }
  const gusts = gustRange(weather);
  const missing = weather.areasWithoutWeather ?? 0;
  return (
    <div className="weather-status">
      <p className="field__hint">
        <strong>Forecast for the next 48 hours.</strong>{" "}
        {gusts ? `Peak wind gusts across the areas: ${gusts}. ` : ""}
        {weather.severity.multiplierMax !== undefined && weather.severity.multiplierMax <= 0.25
          ? "That is calm enough that the model rates the storm at its mildest."
          : "The model turns the strongest gust in each area into a storm strength."}
        {missing > 0 ? ` ${missing} area${missing === 1 ? "" : "s"} had no forecast and use a typical storm.` : ""}
      </p>
      <details className="weather-status__how">
        <summary>How is the forecast used?</summary>
        <p>{weather.severity.method}</p>
        {weather.severity.anchors && (
          <ul className="plain">
            {weather.severity.anchors.map((anchor) => (
              <li key={anchor.storm}>
                {anchor.storm}: gusts reached {anchor.peakGustMph} mph
              </li>
            ))}
          </ul>
        )}
        <p>
          Only wind is counted. Ice, snow, rain and lightning are shown for awareness but do not change the rating.
          This is an assumption, not a fitted outage model.
        </p>
      </details>
      <p className="weather-status__credit">{weather.attribution}</p>
    </div>
  );
}

/**
 * Storm strength: either what the live forecast says, or a past storm the person picks to explore "what if".
 * Either way it is labelled so nobody mistakes a scenario for a forecast, or the reverse.
 */
export function ScenarioControls({ severity, updating, weather, onChange }: Props) {
  const lastPreset = useRef<ScenarioPreset>(DEFAULT_PRESET);
  const isWeather = severity === "weather";
  if (!isWeather) lastPreset.current = severity;

  const index = Math.max(
    0,
    SEVERITY_STEPS.findIndex((step) => step.id === lastPreset.current),
  );
  const current = SEVERITY_STEPS[index];

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    const next = SEVERITY_STEPS[Number(event.target.value)];
    if (next) onChange(next.id);
  };

  return (
    <div className="scenario">
      <div className="tiprow">
        <span className="field__label" aria-hidden="true">
          Storm strength
        </span>
        <InfoTip label="storm strength">{STORM_STRENGTH_HELP}</InfoTip>
      </div>
      <fieldset className="mode">
        <legend className="sr-only">Storm strength</legend>
        <div className="mode__options">
          <label className={`mode__option${isWeather ? " is-selected" : ""}`}>
            <input
              type="radio"
              name="storm-mode"
              checked={isWeather}
              onChange={() => onChange("weather")}
            />
            <CloudIcon size={18} />
            <span>Live weather</span>
          </label>
          <label className={`mode__option${!isWeather ? " is-selected" : ""}`}>
            <input
              type="radio"
              name="storm-mode"
              checked={!isWeather}
              onChange={() => onChange(lastPreset.current)}
            />
            <span>What-if storm</span>
          </label>
        </div>
      </fieldset>

      {isWeather ? (
        <WeatherStatus weather={weather} updating={updating} />
      ) : (
        <>
          <input
            id="severity-range"
            className="scenario__range"
            type="range"
            min={0}
            max={SEVERITY_STEPS.length - 1}
            step={1}
            value={index}
            onChange={handleChange}
            aria-label="Storm scenario"
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
            <span className="scenario__assumption">This is a scenario you choose, not a weather forecast.</span>
          </p>
        </>
      )}
      <p className="sr-only" role="status">
        {updating ? "Updating the map for this storm." : ""}
      </p>
    </div>
  );
}
