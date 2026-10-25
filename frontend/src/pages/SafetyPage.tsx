import { DOWNED_LINES, EMERGENCY, GENERATOR_CO } from "../lib/chatAnswer";
import { internalLink, type RoutePath } from "../lib/useRoute";
import "./InfoPage.css";

/**
 * Storm and outage safety. The three safety sentences are the same fixed text the assistant adds to its answers
 * (`backend/concierge/assistant/safety.py`), so the site never says two different things.
 */
export function SafetyPage({ onNavigate }: { onNavigate: (to: RoutePath) => void }) {
  return (
    <main id="main" className="info-page" tabIndex={-1}>
      <div className="container info-page__inner">
        <h1>Safety</h1>
        <p className="info-page__callout" role="note">
          <strong>{EMERGENCY}</strong>
        </p>

        <h2>Downed power lines</h2>
        <p>{DOWNED_LINES}</p>

        <h2>Generators, grills and camp stoves</h2>
        <p>{GENERATOR_CO}</p>

        <h2>If the power goes out</h2>
        <ul>
          <li>Report your outage through your utility&rsquo;s official outage line or app.</li>
          <li>Keep the refrigerator and freezer doors closed so food stays cold as long as possible.</li>
          <li>Use flashlights or battery lanterns for light, and avoid candles.</li>
        </ul>

        <p>
          Need to talk to a person? Call <a href="tel:8006722231">800-672-2231</a>.
        </p>

        <div className="info-page__actions">
          <a className="btn btn-danger" href="/map" onClick={internalLink(onNavigate, "/map")}>
            See what to expect in my area
          </a>
          <a className="btn btn-outline-danger" href="/" onClick={internalLink(onNavigate, "/")}>
            Ask the Concierge
          </a>
        </div>
      </div>
    </main>
  );
}
