import { useId, type ReactNode } from "react";
import { InfoTip } from "../InfoTip";
import { MinusIcon, PlusIcon } from "../icons";
import "./panel.css";

interface Props {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
  /** Singular noun used in the button names, such as "crew". */
  unit?: string;
  /** Plain-language help shown in a circled "i" beside the label. */
  info?: ReactNode;
}

/** A plus/minus counter with real buttons, large touch targets, and a labelled value. */
export function Stepper({ label, value, min, max, onChange, unit, info }: Props) {
  const labelId = useId();
  const noun = unit ?? label.toLowerCase();

  return (
    <div className="stepper" role="group" aria-labelledby={labelId}>
      <span className="stepper__name">
        <span id={labelId} className="stepper__label">
          {label}
        </span>
        {info && <InfoTip label={label.toLowerCase()}>{info}</InfoTip>}
      </span>
      <span className="stepper__controls">
        <button
          type="button"
          className="stepper__btn"
          onClick={() => onChange(Math.max(min, value - 1))}
          disabled={value <= min}
        >
          <MinusIcon size={18} />
          <span className="sr-only">Decrease {noun}</span>
        </button>
        <output className="stepper__value" aria-live="polite">
          {value}
        </output>
        <button
          type="button"
          className="stepper__btn"
          onClick={() => onChange(Math.min(max, value + 1))}
          disabled={value >= max}
        >
          <PlusIcon size={18} />
          <span className="sr-only">Increase {noun}</span>
        </button>
      </span>
    </div>
  );
}
