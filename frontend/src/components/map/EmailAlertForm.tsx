import { useState, type FormEvent } from "react";
import { conciergeClient } from "../../api";
import type { AlertEmailResult, AlertEmailTarget, ConciergeClient } from "../../api";
import { ApiError } from "../../api/httpClient";
import { CheckIcon, InfoIcon, WarningIcon } from "../icons";
import "./panel.css";

interface Props {
  target: AlertEmailTarget;
  client?: ConciergeClient;
}

const STATUS_CLASS: Record<AlertEmailResult["status"], string> = {
  sent: "email-status email-status--sent",
  not_configured: "email-status email-status--warn",
  failed: "email-status email-status--error",
  rate_limited: "email-status email-status--error",
};

/** The email exactly as it was written (and, when the status is "sent", as it went out), so nobody has to guess. */
function EmailPreview({ result }: { result: AlertEmailResult }) {
  return (
    <figure className="email-preview">
      <figcaption className="email-preview__head">
        <span className="email-preview__row">
          <span className="email-preview__label">From</span> {result.fromName}
        </span>
        <span className="email-preview__row">
          <span className="email-preview__label">To</span> {result.to}
        </span>
        <span className="email-preview__row">
          <span className="email-preview__label">Subject</span> <strong>{result.email.subject}</strong>
        </span>
      </figcaption>
      <pre className="email-preview__body">{result.email.body}</pre>
    </figure>
  );
}

/**
 * "Email me this alert". One address, one consent box, one real email. The address goes to the server only to send this
 * message and is not kept. Afterwards the page shows the email that was written, and says plainly if it was not sent.
 */
export function EmailAlertForm({ target, client = conciergeClient }: Props) {
  const [email, setEmail] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AlertEmailResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim()) {
      setProblem("Enter your email address.");
      return;
    }
    if (!consent) {
      setProblem("Tick the box to say you want this email.");
      return;
    }
    setBusy(true);
    setProblem(null);
    setResult(null);
    try {
      setResult(await client.sendAlertEmail(email.trim(), target));
    } catch (error) {
      setProblem(
        error instanceof ApiError && error.status === 400
          ? error.message
          : "We couldn't send that just now. Please try again in a moment.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="email-form" aria-labelledby="email-form-title">
      <h4 id="email-form-title" className="card__subhead">
        Email me this alert
      </h4>
      <form onSubmit={handleSubmit} noValidate>
        <label className="field__label" htmlFor="alert-email">
          Your email address
        </label>
        <div className="finder__row">
          <input
            id="alert-email"
            className="form-control"
            type="email"
            name="email"
            autoComplete="email"
            maxLength={254}
            placeholder="name@example.com"
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              if (problem) setProblem(null);
            }}
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? "alert-email-problem" : "alert-email-hint"}
          />
          <button type="submit" className="btn btn-outline-danger" disabled={busy}>
            {busy ? "Sending…" : "Send email"}
          </button>
        </div>
        <label className="choice__option email-form__consent">
          <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
          <span>Yes, send me one email now with this alert.</span>
        </label>
        <p id="alert-email-hint" className="field__hint">
          One email, sent once. We use your address only to send it and don&rsquo;t save it. It is a model estimate,
          not confirmed utility information.
        </p>
        {problem && (
          <p id="alert-email-problem" className="finder__message finder__message--error" role="alert">
            {problem}
          </p>
        )}
      </form>

      {result && (
        <div aria-live="polite">
          <p className={STATUS_CLASS[result.status]} role="status">
            {result.status === "sent" ? (
              <CheckIcon size={20} />
            ) : result.status === "not_configured" ? (
              <InfoIcon size={20} />
            ) : (
              <WarningIcon size={20} />
            )}
            <span>{result.message}</span>
          </p>
          <EmailPreview result={result} />
        </div>
      )}
    </section>
  );
}
