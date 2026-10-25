import { internalLink, type RoutePath } from "../lib/useRoute";
import "./InfoPage.css";

/** What this prototype is, what it uses, and what it can't do. Credits are plain text on purpose: no outside links. */
export function AboutPage({ onNavigate }: { onNavigate: (to: RoutePath) => void }) {
  return (
    <main id="main" className="info-page" tabIndex={-1}>
      <div className="container info-page__inner">
        <h1>About this prototype</h1>
        <p>
          The Outage Concierge is a HackOHI/O 2026 prototype for the AEP challenge. A customer picks a place before a
          storm and gets four answers: will I lose power, how long could restoration take, what should I do, and do I
          qualify for alerts.
        </p>
        <p className="info-page__callout" role="note">
          <strong>Model estimates, not confirmed information.</strong> Nothing here is a confirmed utility restoration
          time, and this is not an official Ohio Power Company service.
        </p>

        <h2>Where it works</h2>
        <p>
          The outage data behind this prototype covers one service territory: the Roanoke Valley and the communities
          around it. Places and ZIP codes outside that territory can&rsquo;t be answered yet. The assistant tells you
          when that is the case instead of guessing.
        </p>

        <h2>How the answers are made</h2>
        <ul>
          <li>Code works out every number. The language model only helps phrase the text, and it never sees or writes a number.</li>
          <li>Each area is rated through one typical home in it, using damage reports from two past storms.</li>
          <li>Storm strength can come from a live weather forecast, or from a past storm you pick on the map.</li>
        </ul>

        <h2>Your privacy</h2>
        <ul>
          <li>You only give a city or ZIP code. Please don&rsquo;t type a street address or account number.</li>
          <li>Map areas cover at least 10 homes. No address, meter, device or circuit identifier is ever shown.</li>
          <li>Area names come from public place names near the area. They are not customer data.</li>
        </ul>

        <h2>Credits</h2>
        <ul>
          <li>Map background: OpenStreetMap contributors. Map display: Leaflet.</li>
          <li>Weather: Open-Meteo.com (CC BY 4.0). Forecasts can change and are not an official warning.</li>
          <li>Place names: OpenStreetMap contributors. ZIP code centres: US Census Bureau.</li>
        </ul>

        <div className="info-page__actions">
          <a className="btn btn-danger" href="/map" onClick={internalLink(onNavigate, "/map")}>
            Open the outage map
          </a>
          <a className="btn btn-outline-danger" href="/safety" onClick={internalLink(onNavigate, "/safety")}>
            Safety information
          </a>
        </div>
      </div>
    </main>
  );
}
