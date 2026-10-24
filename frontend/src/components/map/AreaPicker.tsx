import type { ChangeEvent } from "react";
import type { MapZone, RiskBand } from "../../api";
import { BAND_LABEL, NOT_RATED_LABEL } from "../../lib/bands";
import "./panel.css";

interface Props {
  zones: MapZone[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

const GROUPS: ReadonlyArray<{ band: RiskBand | null; label: string }> = [
  { band: "High", label: BAND_LABEL.High },
  { band: "Medium", label: BAND_LABEL.Medium },
  { band: "Low", label: BAND_LABEL.Low },
  { band: null, label: NOT_RATED_LABEL },
];

/**
 * The accessible way to choose an area: a native select, grouped by risk, that stays in step with
 * the map. Works with keyboard, screen readers and touch.
 */
export function AreaPicker({ zones, selectedId, onSelect }: Props) {
  const handleChange = (event: ChangeEvent<HTMLSelectElement>) => {
    if (event.target.value) onSelect(event.target.value);
  };

  return (
    <div className="picker">
      <label className="field__label" htmlFor="area-select">
        Choose an area
      </label>
      <select
        id="area-select"
        className="form-control picker__select"
        value={selectedId ?? ""}
        onChange={handleChange}
        aria-describedby="area-select-hint"
      >
        <option value="" disabled>
          Select an area
        </option>
        {GROUPS.map((group) => {
          const inGroup = zones.filter((zone) => zone.band === group.band);
          if (inGroup.length === 0) return null;
          return (
            <optgroup key={group.label} label={group.label}>
              {inGroup.map((zone) => (
                <option key={zone.id} value={zone.id}>
                  {zone.name}
                </option>
              ))}
            </optgroup>
          );
        })}
      </select>
      <p id="area-select-hint" className="field__hint">
        You can also click an area on the map. Areas are grouped by risk in the scenario below.
      </p>
    </div>
  );
}
