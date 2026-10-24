import { RiskBadge } from "./Card";
import "./panel.css";

/** Key to the map colors, plus the privacy promise about what an "area" is. */
export function MapLegend({ minHomesPerArea }: { minHomesPerArea: number }) {
  return (
    <div className="legend">
      <p className="legend__title">Risk in this storm scenario</p>
      <ul className="legend__list">
        <li>
          <RiskBadge band="Low" size="sm" />
        </li>
        <li>
          <RiskBadge band="Medium" size="sm" />
        </li>
        <li>
          <RiskBadge band="High" size="sm" />
        </li>
        <li>
          <RiskBadge band={null} size="sm" />
        </li>
      </ul>
      <p className="legend__note">
        Each area covers at least {minHomesPerArea} homes. The map never shows or asks for addresses, and it
        can&rsquo;t zoom to street level.
      </p>
    </div>
  );
}
