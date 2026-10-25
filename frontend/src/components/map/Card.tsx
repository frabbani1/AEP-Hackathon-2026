import type { ReactNode } from "react";
import type { RiskBand } from "../../api";
import { BAND_BARS, BAND_LABEL, NOT_RATED_LABEL } from "../../lib/bands";
import { TAG_HELP } from "../../lib/explain";
import { InfoTip, TitleWithTip } from "../InfoTip";
import "./panel.css";

/** A titled block of the area panel. The heading is the customer's own question. */
export function Card({
  id,
  title,
  tag,
  info,
  children,
}: {
  id: string;
  title: string;
  tag?: ReactNode;
  /** Plain-language help shown in a circled "i" beside the title. */
  info?: ReactNode;
  children: ReactNode;
}) {
  const heading = (
    <h3 id={`${id}-title`} className="card__title">
      {title}
    </h3>
  );
  return (
    <section className="card" aria-labelledby={`${id}-title`}>
      <div className="card__head">
        {info ? <TitleWithTip tip={<InfoTip label={title}>{info}</InfoTip>}>{heading}</TitleWithTip> : heading}
        {tag}
      </div>
      {children}
    </section>
  );
}

/** A small heading inside a card, with an optional circled "i" beside it. */
export function SubHead({ children, info, label }: { children: string; info?: ReactNode; label?: string }) {
  const heading = <h4 className="card__subhead">{children}</h4>;
  if (!info) return heading;
  return (
    <TitleWithTip sub tip={<InfoTip label={label ?? children.toLowerCase()}>{info}</InfoTip>}>
      {heading}
    </TitleWithTip>
  );
}

export type TagKind = "model" | "confirmed" | "actions" | "rules";

const TAG_TEXT: Record<TagKind, string> = {
  model: "Model estimate",
  confirmed: "Confirmed by utility",
  actions: "Your actions",
  rules: "Rule-based",
};

/** Says where a piece of information comes from, so facts, estimates and actions stay separate. The "i" explains it. */
export function Tag({ kind }: { kind: TagKind }) {
  return (
    <span className="tagwrap">
      <span className={`tag tag--${kind}`}>{TAG_TEXT[kind]}</span>
      <InfoTip label={TAG_TEXT[kind].toLowerCase()}>{TAG_HELP[kind]}</InfoTip>
    </span>
  );
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
