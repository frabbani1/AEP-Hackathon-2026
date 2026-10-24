import type { Transparency } from "../../api";
import { PhoneIcon } from "../icons";
import { Card } from "./Card";

function Block({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <>
      <h4 className="card__subhead">{title}</h4>
      <ul className="plain">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </>
  );
}

/** Assumptions, gaps, and what could change the answer, plus how privacy and escalation are handled. */
export function TransparencyCard({
  transparency,
  minHomesPerArea,
}: {
  transparency: Transparency;
  minHomesPerArea: number | null;
}) {
  return (
    <Card id="about" title="What this answer does and doesn't know">
      <Block title="What we assumed" items={transparency.assumptions} />
      <Block title="What we don't have" items={transparency.missingData} />
      <Block title="What could change this answer" items={transparency.couldChange} />

      <h4 className="card__subhead">How we protect your privacy</h4>
      <ul className="plain">
        <li>We don&rsquo;t use or show addresses, account numbers, or exact locations.</li>
        {minHomesPerArea !== null && (
          <li>Each area covers at least {minHomesPerArea} homes, so no single home can be picked out.</li>
        )}
        <li>We name a nearby hospital, fire station, or ambulance service only as &ldquo;critical facility nearby&rdquo;.</li>
      </ul>

      <p className="callout callout--plain">
        <PhoneIcon size={20} />
        <span>
          Not sure about an answer, or need help? Talk to a person at <a href="tel:8006722231">800-672-2231</a>.
        </span>
      </p>
    </Card>
  );
}
