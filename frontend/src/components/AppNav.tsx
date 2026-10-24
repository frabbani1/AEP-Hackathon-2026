import type { MouseEvent, ReactNode } from "react";
import type { RoutePath } from "../lib/useRoute";
import { ChatIcon, MapIcon } from "./icons";
import "./AppNav.css";

interface Props {
  path: RoutePath;
  onNavigate: (to: RoutePath) => void;
}

const ITEMS: ReadonlyArray<{ to: RoutePath; label: string; icon: ReactNode }> = [
  { to: "/", label: "Ask the Concierge", icon: <ChatIcon size={20} /> },
  { to: "/map", label: "Outage map", icon: <MapIcon size={20} /> },
];

/** Switches between the two pages of the app. Real links, so open-in-new-tab still works. */
export function AppNav({ path, onNavigate }: Props) {
  const handleClick = (event: MouseEvent<HTMLAnchorElement>, to: RoutePath) => {
    // Let the browser handle new-tab clicks and other modified clicks.
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    onNavigate(to);
  };

  return (
    <nav className="appnav" aria-label="Outage Concierge">
      <div className="container">
        <ul className="appnav__list">
          {ITEMS.map((item) => (
            <li key={item.to}>
              <a
                href={item.to}
                className="appnav__link"
                aria-current={path === item.to ? "page" : undefined}
                onClick={(event) => handleClick(event, item.to)}
              >
                {item.icon}
                {item.label}
              </a>
            </li>
          ))}
        </ul>
      </div>
    </nav>
  );
}
