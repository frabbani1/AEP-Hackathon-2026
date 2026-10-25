import type { ZoneWeather } from "../../api";
import { STORM_MULTIPLIER_HELP, WEATHER_GUST_HELP } from "../../lib/explain";
import { InfoTip } from "../InfoTip";
import { Card } from "./Card";
import { WarningIcon } from "../icons";

const num = (value: number | null | undefined, unit: string) =>
  value === null || value === undefined ? "n/a" : `${Math.round(value)}${unit}`;

function Stat({ label, value, sub, info }: { label: string; value: string; sub?: string; info?: string }) {
  return (
    <div className="weather-grid__item">
      <div className="weather-grid__label">
        {label}
        {info && <InfoTip label={label.toLowerCase()}>{info}</InfoTip>}
      </div>
      <div className="weather-grid__value">{value}</div>
      {sub && <div className="weather-grid__sub">{sub}</div>}
    </div>
  );
}

/** Real conditions and forecast for the area, and how they set the storm strength used in the answers below. */
export function WeatherCard({ weather }: { weather: ZoneWeather }) {
  if (weather.status !== "ok") {
    return (
      <Card id="weather" title="Weather for this area">
        <p className="callout callout--warn">
          <WarningIcon size={20} />
          <span>{weather.notice ?? "Weather could not be loaded, so a typical storm was used instead."}</span>
        </p>
        <p className="weather-status__credit">{weather.attribution}</p>
      </Card>
    );
  }

  const { current, ahead48h, hazards = [], severity } = weather;
  const usedWeather = severity.source === "weather";

  return (
    <Card id="weather" title="Weather for this area">
      <div className="weather-grid">
        {current && (
          <Stat
            label="Now"
            value={num(current.tempF, "\u00b0F")}
            sub={[current.conditions, current.windMph != null ? `wind ${num(current.windMph, " mph")}` : null]
              .filter(Boolean)
              .join(", ")}
          />
        )}
        {ahead48h && (
          <Stat
            label="Peak gust, next 48 hours"
            info={WEATHER_GUST_HELP}
            value={num(ahead48h.peakGustMph, " mph")}
            sub={ahead48h.precipIn ? `${ahead48h.precipIn.toFixed(1)} in of rain expected` : "No significant rain expected"}
          />
        )}
        {usedWeather && severity.multiplier !== undefined && (
          <Stat
            label="Storm strength used"
            info={STORM_MULTIPLIER_HELP}
            value={`${severity.multiplier.toFixed(2)}\u00d7`}
            sub="vs. a typical past storm"
          />
        )}
      </div>

      {hazards.map((hazard) => (
        <p key={hazard.id} className={`hazard${hazard.level === "severe" ? " hazard--severe" : ""}`}>
          <WarningIcon size={20} />
          <span>
            {hazard.text}
            {!hazard.modelled && (
              <span className="hazard__note">Shown for awareness. It does not change the rating below.</span>
            )}
          </span>
        </p>
      ))}

      <p className="card__muted">
        {usedWeather
          ? "The risk and restoration answers below use this forecast. "
          : "Weather could not set the storm strength, so a typical storm was used. "}
        {weather.basis}
      </p>
      <p className="weather-status__credit">{weather.attribution}</p>
    </Card>
  );
}
