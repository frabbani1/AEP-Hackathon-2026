import { useState, type FormEvent } from "react";
import type { ConciergeClient, WeatherOutlook } from "../../api";
import { ApiError } from "../../api/httpClient";
import { FIND_AREA_HELP } from "../../lib/explain";
import { parseLocation } from "../../lib/parseLocation";
import { InfoTip } from "../InfoTip";
import "./panel.css";

interface Props {
  client: ConciergeClient;
  /** Called with the area the typed city or ZIP code belongs to. */
  onFound: (zoneId: string) => void;
  /** Called with a forecast-only outlook when the place has no outage data. */
  onOutlook: (outlook: WeatherOutlook) => void;
}

type Tone = "ok" | "error" | "forecast";

const MESSAGE_CLASS: Record<Tone, string> = {
  ok: "finder__message",
  error: "finder__message finder__message--error",
  forecast: "finder__message finder__message--forecast",
};

/**
 * Jump to an area by city or ZIP code instead of hunting for it on the map or in the list. A place outside the outage
 * data gets a forecast-only outlook instead of a dead end, shown in purple and labeled as a prediction.
 */
export function AreaFinder({ client, onFound, onOutlook }: Props) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; tone: Tone } | null>(null);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const parsed = parseLocation(draft);
    if (!parsed.ok) {
      setMessage({ text: parsed.error, tone: "error" });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const result = await client.findArea(parsed.value);
      if (result.status === "found") {
        onFound(result.zoneId);
        setMessage({
          text: result.exact
            ? `Showing the ${result.zoneName} area.`
            : `The closest area I have is ${result.zoneName}, so that is what is shown.`,
          tone: "ok",
        });
        return;
      }

      // No outage data here. Fall back to what the weather forecast can say, and label it as a prediction.
      const outlook = await client.getOutlook(parsed.value);
      if (outlook.status === "ok") {
        onOutlook(outlook);
        setMessage({
          text: `No outage data for ${outlook.place.name}. Showing a forecast-only outlook, which is a prediction.`,
          tone: "forecast",
        });
      } else {
        const places = result.coveredPlaces.slice(0, 4).join(", ");
        setMessage({
          text: `We couldn't find ${parsed.value}. Try a US city or ZIP code. Outage data covers the Roanoke Valley${places ? `, for example ${places}` : ""}.`,
          tone: "error",
        });
      }
    } catch (error) {
      setMessage({
        text:
          error instanceof ApiError && error.status === 400
            ? error.message
            : "We couldn't look that up. Try again in a moment.",
        tone: "error",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="finder" onSubmit={handleSubmit} noValidate>
      <div className="tiprow">
        <label className="field__label" htmlFor="area-finder">
          Find your area by city or ZIP code
        </label>
        <InfoTip label="finding your area">{FIND_AREA_HELP}</InfoTip>
      </div>
      <div className="finder__row">
        <input
          id="area-finder"
          className="form-control"
          type="text"
          autoComplete="off"
          maxLength={60}
          placeholder="City or ZIP code"
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            if (message) setMessage(null);
          }}
          aria-describedby={message ? "area-finder-message" : undefined}
          aria-invalid={message?.tone === "error" ? true : undefined}
        />
        <button type="submit" className="btn btn-outline-danger" disabled={busy}>
          {busy ? "Looking…" : "Find"}
        </button>
      </div>
      <p
        id="area-finder-message"
        className={message ? MESSAGE_CLASS[message.tone] : "finder__message"}
        role={message?.tone === "error" ? "alert" : "status"}
      >
        {message?.text}
      </p>
    </form>
  );
}
