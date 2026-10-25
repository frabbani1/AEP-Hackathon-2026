import { useState } from "react";
import type { WeatherOutlook } from "../../api";
import { OUTLOOK_LEVEL_HELP, TAG_HELP, WEATHER_GUST_HELP } from "../../lib/explain";
import { InfoTip } from "../InfoTip";
import { InfoIcon, WarningIcon } from "../icons";
import { EmailAlertForm } from "./EmailAlertForm";
import "./panel.css";

const num = (value: number | null | undefined, unit: string) =>
  value === null || value === undefined ? "n/a" : `${Math.round(value)}${unit}`;

/** Purple label that goes wherever a forecast-only prediction appears, so it is never mistaken for outage data. */
export function PredictionTag({
  children = "Prediction only \u00b7 no outage data",
  help = TAG_HELP.forecast,
}: {
  children?: string;
  /** What the "i" says. Defaults to the explanation of a forecast-only prediction. */
  help?: string;
}) {
  return (
    <span className="tagwrap">
      <span className="tag tag--forecast">{children}</span>
      <InfoTip label={children.toLowerCase()}>{help}</InfoTip>
    </span>
  );
}

/** Four-step meter in words and bars, so the level is never shown by color alone. */
function LevelBadge({ outlook }: { outlook: WeatherOutlook["outlook"] }) {
  return (
    <span className={`forecast-badge forecast-badge--${outlook.level}`}>
      <span className="forecast-badge__bars" aria-hidden="true">
        {Array.from({ length: outlook.of }, (_, index) => (
          <span key={index} className={index < outlook.steps ? "is-on" : undefined} />
        ))}
      </span>
      <span>Weather level: {outlook.label}</span>
      <InfoTip label="weather level">{OUTLOOK_LEVEL_HELP}</InfoTip>
    </span>
  );
}

/**
 * The answer for a place with no outage data. Everything here comes from the weather forecast. It is drawn in its own
 * purple, with a "Prediction only" label, and it lists what it cannot tell you, so it can't pass for the outage report.
 */
export function OutlookCard({ outlook, onClose }: { outlook: WeatherOutlook; onClose: () => void }) {
  // Ticks stay on this device and reset when the page reloads.
  const [done, setDone] = useState<ReadonlySet<string>>(new Set());
  const toggle = (id: string) =>
    setDone((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const { place, weather, preparation } = outlook;
  const level = outlook.outlook;
  const now = weather.current;

  return (
    <div className="outlook" aria-labelledby="outlook-title">
      <p className="sr-only" role="status">
        {`${place.name}: forecast-only outlook, weather level ${level.label}. Prediction only, with no outage data.`}
      </p>

      <div className="outlook__banner">
        <PredictionTag />
        <button type="button" className="link-button" onClick={onClose}>
          Back to the outage map
        </button>
      </div>

      <h2 id="outlook-title" className="outlook__title">
        {place.name}
      </h2>

      <p className="callout outlook__notice">
        <InfoIcon size={20} />
        <span>{outlook.notice}</span>
      </p>

      <section className="outlook__section" aria-labelledby="outlook-forecast">
        <div className="card__head">
          <h3 id="outlook-forecast" className="card__title">
            What the forecast says
          </h3>
        </div>
        <LevelBadge outlook={level} />
        <p className="outlook__summary">{level.summary}</p>

        <div className="weather-grid">
          {now && (
            <div className="weather-grid__item">
              <div className="weather-grid__label">Now</div>
              <div className="weather-grid__value">{num(now.tempF, "\u00b0F")}</div>
              <div className="weather-grid__sub">
                {[now.conditions, now.windMph != null ? `wind ${num(now.windMph, " mph")}` : null]
                  .filter(Boolean)
                  .join(", ")}
              </div>
            </div>
          )}
          <div className="weather-grid__item">
            <div className="weather-grid__label">
              Peak gust, next 48 hours
              <InfoTip label="peak gust">{WEATHER_GUST_HELP}</InfoTip>
            </div>
            <div className="weather-grid__value">{num(level.peakGustMph, " mph")}</div>
          </div>
          {weather.ahead48h && (
            <div className="weather-grid__item">
              <div className="weather-grid__label">Rain, next 48 hours</div>
              <div className="weather-grid__value">{(weather.ahead48h.precipIn ?? 0).toFixed(1)} in</div>
            </div>
          )}
        </div>

        {outlook.hazards.map((hazard) => (
          <p key={hazard.id} className="hazard hazard--forecast">
            <WarningIcon size={20} />
            <span>{hazard.text}</span>
          </p>
        ))}

        <p className="card__muted">{level.method}</p>
      </section>

      <section className="outlook__section outlook__section--missing" aria-labelledby="outlook-missing">
        <h3 id="outlook-missing" className="card__title">
          What this does not tell you
        </h3>
        <p className="card__muted">
          There are no outage records for {place.name}, so these are not shown. We won&rsquo;t guess them.
        </p>
        <ul className="outlook__missing">
          {outlook.notAvailable.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>

      <section className="outlook__section" aria-labelledby="outlook-prepare">
        <div className="card__head">
          <h3 id="outlook-prepare" className="card__title">
            How to get ready
          </h3>
          <PredictionTag help="Common-sense steps for any storm. They aren't based on outage records for this place.">
            General advice
          </PredictionTag>
        </div>
        <ul className="safety">
          {preparation.safety.map((notice) => (
            <li key={notice.id} className={`safety__item safety__item--${notice.level}`}>
              <WarningIcon size={22} />
              <div>
                <p className="safety__label">{notice.level === "emergency" ? "Emergency" : "Safety"}</p>
                <p className="safety__title">{notice.title}</p>
                <p className="safety__text">{notice.text}</p>
              </div>
            </li>
          ))}
        </ul>
        <ul className="prep__list">
          {preparation.actions.map((action) => {
            const inputId = `outlook-${action.id}`;
            return (
              <li key={action.id} className="prep__item">
                <input id={inputId} type="checkbox" checked={done.has(action.id)} onChange={() => toggle(action.id)} />
                <label htmlFor={inputId}>{action.text}</label>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="outlook__section">
        {/* The place name (such as "Dublin, Ohio") looks up the same place again on the server. */}
        <EmailAlertForm key={place.name} target={{ kind: "outlook", query: place.name }} />
      </section>

      <p className="weather-status__credit">
        {weather.attribution}. {weather.basis}
      </p>
    </div>
  );
}
