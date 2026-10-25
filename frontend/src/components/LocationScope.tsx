import { useState, type FormEvent } from "react";
import { LOCATION_CHIP_HELP, LOCATION_HELP } from "../lib/explain";
import { parseLocation } from "../lib/parseLocation";
import { InfoTip } from "./InfoTip";
import { CloseIcon, PinIcon } from "./icons";
import "./LocationScope.css";

interface Props {
  location: string | null;
  /** The map area the location was matched to. */
  areaName: string | null;
  /** Resolves to a message to show beside the box when the location could not be used, or null when it worked. */
  onChange: (location: string | null) => Promise<string | null>;
}

export function LocationScope({ location, areaName, onChange }: Props) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const result = parseLocation(draft);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setError(null);
    setDraft(result.value);
    if (result.value === location) return;
    setChecking(true);
    const issue = await onChange(result.value);
    setChecking(false);
    if (issue) setError(issue);
  };

  const handleClear = () => {
    setDraft("");
    setError(null);
    void onChange(null);
  };

  return (
    <section className="scope" aria-label="Location">
      <div className="container scope__inner">
        <form className="scope__form" onSubmit={handleSubmit} noValidate>
          <div className="tiprow">
            <label className="scope__label" htmlFor="location-input">
              Your location
            </label>
            <InfoTip label="your location">{LOCATION_HELP}</InfoTip>
          </div>
          <div className="scope__row">
            <input
              id="location-input"
              className="form-control scope__input"
              type="text"
              name="location"
              autoComplete="off"
              maxLength={60}
              placeholder="City or ZIP code"
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value);
                if (error) setError(null);
              }}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "location-hint location-error" : "location-hint"}
            />
            <button type="submit" className="btn btn-outline-danger scope__submit" disabled={checking}>
              {checking ? "Checking…" : "Set location"}
            </button>
          </div>
          <p id="location-hint" className="scope__hint">
            A city or ZIP code is all I need. Please don&rsquo;t enter your street address.
          </p>
          {error && (
            <p id="location-error" className="scope__error" role="alert">
              {error}
            </p>
          )}
        </form>

        {location && (
          <p className="scope__chip">
            <PinIcon size={18} />
            <span>
              Scoped to <strong>{location}</strong>
              {areaName && areaName !== location && <> &middot; {areaName} area</>}
            </span>
            <InfoTip label="your location scope">{LOCATION_CHIP_HELP}</InfoTip>
            <button type="button" className="scope__clear" onClick={handleClear}>
              <CloseIcon size={16} />
              <span className="sr-only">Clear location</span>
            </button>
          </p>
        )}
      </div>
    </section>
  );
}
