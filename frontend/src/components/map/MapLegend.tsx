import { LEGEND_HELP } from "../../lib/explain";
import { InfoTip } from "../InfoTip";
import { RiskBadge } from "./Card";
import { PredictionTag } from "./OutlookCard";
import "./panel.css";

/** Key to the map colors, plus the privacy promise about what an "area" is. */
export function MapLegend({ minHomesPerArea }: { minHomesPerArea: number }) {
  return (
    <div className="legend">
      <div className="tiprow">
        <p className="legend__title">Risk in this storm scenario</p>
        <InfoTip label="the map colors">{LEGEND_HELP}</InfoTip>
      </div>
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
      <p className="legend__forecast">
        <PredictionTag />
        <span>Purple is a forecast-only outlook for a place with no outage data. It has no risk rating.</span>
      </p>
      <p className="legend__note">
        Each area covers at least {minHomesPerArea} homes and is named for a nearby community. The map never shows or
        asks for addresses, and it can&rsquo;t zoom to street level. Only places with outage data are colored.
      </p>
    </div>
  );
}
