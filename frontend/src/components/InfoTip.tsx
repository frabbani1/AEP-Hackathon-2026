import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { InfoIcon } from "./icons";
import "./InfoTip.css";

interface Props {
  /** What is being explained, such as "confidence". Becomes the button's name: "More about confidence". */
  label: string;
  /** Plain-language explanation. Keep it to a sentence or three. */
  children: ReactNode;
  className?: string;
}

const GAP = 8;
const MARGIN = 8;
const MAX_WIDTH = 288;
const CLOSE_DELAY_MS = 150;

/**
 * A small circled "i" that explains a term or number in plain language.
 *
 * Opens on hover, keyboard focus, or a tap/click (tap again, press Escape, or tap elsewhere to close), so it works
 * with a mouse, a keyboard, a touch screen, and a screen reader. The text is always in the page and linked to the
 * button with aria-describedby. The bubble is drawn in a portal so a card edge never clips it and its words never
 * leak into the accessible name of a heading or fieldset it sits beside.
 */
export function InfoTip({ label, children, className }: Props) {
  const tipId = useId();
  const button = useRef<HTMLButtonElement>(null);
  const bubble = useRef<HTMLSpanElement>(null);
  const closeTimer = useRef<number | undefined>(undefined);

  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [place, setPlace] = useState<{ top: number; left: number; width: number; above: boolean } | null>(null);

  const open = hovered || focused || pinned;

  const cancelClose = () => window.clearTimeout(closeTimer.current);
  const enter = () => {
    cancelClose();
    setHovered(true);
  };
  const leave = () => {
    cancelClose();
    // A short delay lets the pointer cross the gap between the icon and the bubble.
    closeTimer.current = window.setTimeout(() => setHovered(false), CLOSE_DELAY_MS);
  };
  useEffect(() => cancelClose, []);

  // A click or tap keeps the bubble open (that is how touch screens get it). Clicking again closes it, even while the
  // pointer is still over the icon or the icon still has focus.
  const toggle = () => {
    if (open && pinned) {
      cancelClose();
      setPinned(false);
      setHovered(false);
      setFocused(false);
    } else {
      setPinned(true);
    }
  };

  const reposition = useCallback(() => {
    const anchor = button.current;
    const box = bubble.current;
    if (!anchor || !box) return;
    const rect = anchor.getBoundingClientRect();
    const width = Math.min(MAX_WIDTH, window.innerWidth - MARGIN * 2);
    const left = Math.min(
      Math.max(MARGIN, rect.left + rect.width / 2 - width / 2),
      window.innerWidth - width - MARGIN,
    );
    const height = box.offsetHeight;
    const roomBelow = window.innerHeight - rect.bottom - GAP - MARGIN;
    const above = roomBelow < height && rect.top - GAP - MARGIN > roomBelow;
    const top = above ? rect.top - GAP - height : rect.bottom + GAP;
    setPlace({ top, left, width, above });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    reposition();
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [open, reposition]);

  // Escape closes it; so does a press anywhere else (this is how a tap-opened bubble is dismissed on a phone).
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      cancelClose();
      setHovered(false);
      setPinned(false);
    };
    const onPress = (event: PointerEvent) => {
      const target = event.target as Node;
      if (button.current?.contains(target) || bubble.current?.contains(target)) return;
      setHovered(false);
      setPinned(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPress);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPress);
    };
  }, [open]);

  return (
    <>
      <button
        ref={button}
        type="button"
        className={className ? `infotip ${className}` : "infotip"}
        aria-label={`More about ${label}`}
        aria-describedby={tipId}
        onMouseEnter={enter}
        onMouseLeave={leave}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onClick={toggle}
      >
        <InfoIcon size={18} />
      </button>
      {createPortal(
        <span
          ref={bubble}
          id={tipId}
          role="tooltip"
          className={`infotip__bubble${open && place ? " is-open" : ""}${place?.above ? " is-above" : ""}`}
          style={place ? { top: place.top, left: place.left, width: place.width } : undefined}
          onMouseEnter={enter}
          onMouseLeave={leave}
        >
          {children}
        </span>,
        document.body,
      )}
    </>
  );
}

/** A title with its explanation beside it. The tip sits outside the heading so it never becomes part of the heading text. */
export function TitleWithTip({
  children,
  tip,
  sub = false,
}: {
  children: ReactNode;
  tip: ReactNode;
  /** True for a small heading inside a card, which keeps the card's usual spacing around it. */
  sub?: boolean;
}) {
  return (
    <div className={sub ? "titletip titletip--sub" : "titletip"}>
      {children}
      {tip}
    </div>
  );
}
