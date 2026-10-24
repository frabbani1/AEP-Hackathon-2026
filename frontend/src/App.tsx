import { useRef, useState, type FormEvent } from 'react';
import { ApiError, fetchRisk } from './api';
import { SCENARIOS, type RiskResult } from './types';

/**
 * Minimal end-to-end slice: location token + storm scenario -> risk band, confidence and reasons.
 * It shows only text the API returns. It never computes, rounds or rewrites a number.
 * The map picker, restoration range, preparation guidance and messages come with their own changes.
 */
export default function App() {
  const [token, setToken] = useState('');
  const [severity, setSeverity] = useState<string>('typical');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RiskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef<AbortController | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = token.trim();
    if (!trimmed) {
      setError('Enter your location code to continue.');
      return;
    }
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setBusy(true);
    setError(null);
    try {
      setResult(await fetchRisk(trimmed, severity, controller.signal));
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setResult(null);
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.');
    } finally {
      if (inflight.current === controller) setBusy(false);
    }
  }

  return (
    <main>
      <h1>AI Outage Concierge</h1>
      <p className="lead">See how likely a storm is to affect your power, and why. Based on past storm damage, not a weather forecast.</p>

      <form onSubmit={onSubmit} noValidate>
        <div className="field">
          <label htmlFor="token">Location code</label>
          <input
            id="token"
            name="token"
            type="text"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            aria-describedby="token-help"
          />
          <p id="token-help" className="help">An anonymous code for your location. It starts with p_ and is not your address.</p>
        </div>

        <div className="field">
          <label htmlFor="severity">Storm scenario</label>
          <select id="severity" name="severity" value={severity} onChange={(e) => setSeverity(e.target.value)}>
            {SCENARIOS.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </div>

        <button type="submit" disabled={busy}>{busy ? 'Checking…' : 'Check my risk'}</button>
      </form>

      <section aria-live="polite" aria-labelledby="result-heading" className="result">
        <h2 id="result-heading" className="visually-hidden">Your result</h2>
        {error && <p role="alert" className="error">{error}</p>}
        {result && <Result result={result} />}
      </section>

      <footer>
        <p>
          This is a model estimate built from past damage reports. It is not a confirmed utility forecast or restoration time.
          If you see a downed power line, stay away and call 911.
        </p>
      </footer>
    </main>
  );
}

function List({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <>
      <h3>{title}</h3>
      <ul>{items.map((text) => <li key={text}>{text}</li>)}</ul>
    </>
  );
}

function Result({ result }: { result: RiskResult }) {
  if (result.status !== 'ok' || !result.band || !result.confidence) {
    return (
      <div className="card">
        <h3>We can't rate this location</h3>
        <p>{result.fallback?.message ?? 'We could not rate this location. Prepare as if an outage is possible.'}</p>
      </div>
    );
  }
  return (
    <div className="card">
      <p className={`band band-${result.band.toLowerCase()}`}>Outage risk: {result.band}</p>
      <p>
        <strong>Confidence: {result.confidence.level}.</strong> {result.confidence.reason}
      </p>
      {result.label && <p className="note">This is a {result.label}.</p>}
      {result.band_evidence && <p>{result.band_evidence.sentence}</p>}
      <List title="Why" items={result.top_factors.map((f) => f.text)} />
      <List title="What we assumed" items={result.assumptions} />
      <List title="What we don't know" items={result.missing_data} />
      <List title="What could change this" items={result.could_change} />
    </div>
  );
}
