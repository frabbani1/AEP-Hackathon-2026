import { useState } from "react";
import type { DeclaredNeed, Preparation } from "../../api";
import { DECLARED_NEEDS, NEED_LABEL } from "../../lib/scenario";
import { WarningIcon } from "../icons";
import { Card, Tag } from "./Card";

interface Props {
  preparation: Preparation;
  needs: DeclaredNeed[];
  onNeedsChange: (needs: DeclaredNeed[]) => void;
}

/** "What should I do to prepare?" Safety first, then a checklist tailored to what the customer declares. */
export function PrepareCard({ preparation, needs, onNeedsChange }: Props) {
  // Ticks are kept on this device only and reset when the page reloads.
  const [done, setDone] = useState<ReadonlySet<string>>(new Set());

  const toggleDone = (id: string) =>
    setDone((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleNeed = (id: DeclaredNeed) =>
    onNeedsChange(needs.includes(id) ? needs.filter((need) => need !== id) : [...needs, id]);

  return (
    <Card id="prepare" title="What should I do to prepare?" tag={<Tag kind="actions" />}>
      <ul className="safety">
        {preparation.safety.map((notice) => (
          <li key={notice.id} className={`safety__item safety__item--${notice.level}`}>
            <WarningIcon size={22} />
            <div>
              <p className="safety__label">{notice.level === "emergency" ? "Emergency" : "Safety"}</p>
              <p className="safety__title">{notice.title}</p>
              <p className="safety__text">{notice.text}</p>
            </div>
          </li>
        ))}
      </ul>

      <fieldset className="needs">
        <legend className="needs__legend">Optional: tailor this advice</legend>
        <p className="field__hint">
          Only if you want to. We never guess these. They only tailor this page, and we don&rsquo;t keep them.
        </p>
        {DECLARED_NEEDS.map((need) => (
          <label key={need.id} className="choice__option">
            <input type="checkbox" checked={needs.includes(need.id)} onChange={() => toggleNeed(need.id)} />
            <span>{need.label}</span>
          </label>
        ))}
      </fieldset>

      {preparation.groups.map((group) => (
        <section key={group.id} className="prep" aria-labelledby={`prep-${group.id}`}>
          <h4 id={`prep-${group.id}`} className="card__subhead">
            {group.title}
          </h4>
          <ul className="prep__list">
            {group.actions.map((action) => {
              const inputId = `prep-${action.id}`;
              return (
                <li key={action.id} className="prep__item">
                  <input id={inputId} type="checkbox" checked={done.has(action.id)} onChange={() => toggleDone(action.id)} />
                  <label htmlFor={inputId}>
                    {action.text}
                    {action.tailoredFor && <span className="chip chip--need">{NEED_LABEL[action.tailoredFor]}</span>}
                  </label>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </Card>
  );
}
