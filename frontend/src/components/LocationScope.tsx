import { useState, type FormEvent } from "react";
import { parseLocation } from "../lib/parseLocation";
import { CloseIcon, PinIcon } from "./icons";
import "./LocationScope.css";

interface Props {
  location: string | null;
  onChange: (location: string | null) => void;
}

export function LocationScope({ location, onChange }: Props) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    const result = parseLocation(draft);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setError(null);
    setDraft(result.value);
    if (result.value !== location) onChange(result.value);
  };

  const handleClear = () => {
    setDraft("");
    setError(null);
    onChange(null);
  };

  return (
    <section className="scope" aria-label="Location">
      <div className="container scope__inner">
        <form className="scope__form" onSubmit={handleSubmit} noValidate>
          <label className="scope__label" htmlFor="location-input">
            Your location
          </label>
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
            <button type="submit" className="btn btn-outline-danger scope__submit">
              Set location
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
            </span>
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
