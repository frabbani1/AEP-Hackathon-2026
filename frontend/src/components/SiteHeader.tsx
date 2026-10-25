import { useEffect, useRef, useState } from "react";
import { internalLink, type RoutePath } from "../lib/useRoute";
import { CloseIcon, MenuIcon, WarningIcon } from "./icons";
import "./chrome.css";

/** Every link stays inside this app. Nothing here leaves for an outside site. */
const NAV: ReadonlyArray<{ label: string; to: RoutePath }> = [
  { label: "Safety", to: "/safety" },
  { label: "About", to: "/about" },
];

interface Props {
  path: RoutePath;
  onNavigate: (to: RoutePath) => void;
}

export function SiteHeader({ path, onNavigate }: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);

  // Escape closes the mobile menu and returns focus to its toggle.
  useEffect(() => {
    if (!menuOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        toggleRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [menuOpen]);

  const go = (to: RoutePath) => {
    setMenuOpen(false);
    onNavigate(to);
  };

  const links = (
    <>
      {NAV.map((item) => (
        <li key={item.to}>
          <a href={item.to} aria-current={path === item.to ? "page" : undefined} onClick={internalLink(go, item.to)}>
            {item.label}
          </a>
        </li>
      ))}
    </>
  );

  return (
    <header>
      <div className="utility-bar">
        <div className="container utility-bar__inner">
          <p className="utility-bar__text">
            <WarningIcon size={18} />
            <span>
              <strong>Downed power line?</strong> Stay back and call 911.
            </span>
          </p>
        </div>
      </div>

      <div className="site-header">
        <div className="container site-header__inner">
          <a className="site-header__brand" href="/" onClick={internalLink(go, "/")}>
            <img src="/ohio-power-logo.svg" alt="Outage Concierge home" />
          </a>

          <nav className="site-nav" aria-label="More information">
            <ul className="site-nav__list">{links}</ul>
          </nav>

          <button
            ref={toggleRef}
            type="button"
            className="site-header__menu-toggle"
            aria-expanded={menuOpen}
            aria-controls="mobile-nav"
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? <CloseIcon size={26} /> : <MenuIcon size={26} />}
            <span className="sr-only">{menuOpen ? "Close menu" : "Open menu"}</span>
          </button>
        </div>

        <nav id="mobile-nav" className="mobile-nav" aria-label="More information, mobile" hidden={!menuOpen}>
          <div className="container">
            <ul className="mobile-nav__list">{links}</ul>
          </div>
        </nav>
      </div>
    </header>
  );
}
