import type { ReactNode } from "react";
import type { RiskBand } from "../../api";
import { BAND_BARS, BAND_LABEL, NOT_RATED_LABEL } from "../../lib/bands";
import "./panel.css";

/** A titled block of the area panel. The heading is the customer's own question. */
export function Card({
  id,
  title,
  tag,
  children,
}: {
  id: string;
  title: string;
  tag?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card" aria-labelledby={`${id}-title`}>
      <div className="card__head">
        <h3 id={`${id}-title`} className="card__title">
          {title}
        </h3>
        {tag}
      </div>
      {children}
    </section>
  );
}

export type TagKind = "model" | "confirmed" | "actions" | "rules" | "sample";

const TAG_TEXT: Record<TagKind, string> = {
  model: "Model estimate",
  confirmed: "Confirmed by utility",
  actions: "Your actions",
  rules: "Rule-based",
  sample: "Sample data",
};

/** Says where a piece of information comes from, so facts, estimates and actions stay separate. */
export function Tag({ kind }: { kind: TagKind }) {
  return <span className={`tag tag--${kind}`}>{TAG_TEXT[kind]}</span>;
}

/** Risk level as words plus a three-bar meter, so color is never the only signal. */
export function RiskBadge({ band, size = "md" }: { band: RiskBand | null; size?: "sm" | "md" | "lg" }) {
  const bars = band ? BAND_BARS[band] : 0;
  return (
    <span className={`risk-badge risk-badge--${band ? band.toLowerCase() : "none"} risk-badge--${size}`}>
      <span className="risk-badge__bars" aria-hidden="true">
        {[1, 2, 3].map((level) => (
          <span key={level} className={level <= bars ? "is-on" : undefined} />
        ))}
      </span>
      {band ? BAND_LABEL[band] : NOT_RATED_LABEL}
    </span>
  );
}
