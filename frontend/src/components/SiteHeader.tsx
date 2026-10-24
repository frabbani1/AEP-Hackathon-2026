import { useEffect, useRef, useState } from "react";
import { CloseIcon, MenuIcon, WarningIcon } from "./icons";
import "./chrome.css";

const SITE = "https://www.aepohio.com";

const NAV = [
  { label: "My Account", href: `${SITE}/account/` },
  { label: "Outages", href: `${SITE}/outages/` },
  { label: "Savings", href: `${SITE}/savings/` },
  { label: "Community", href: `${SITE}/community/` },
  { label: "Contact", href: `${SITE}/contact` },
];

const externalProps = { target: "_blank", rel: "noopener noreferrer" } as const;

function NewTabNote() {
  return <span className="sr-only"> (opens in a new tab)</span>;
}

export function SiteHeader() {
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
          <a className="site-header__brand" href={SITE} {...externalProps}>
            <img src="/ohio-power-logo.svg" alt="Ohio Power Company home" />
            <NewTabNote />
          </a>

          <nav className="site-nav" aria-label="Ohio Power Company">
            <ul className="site-nav__list">
              {NAV.map((item) => (
                <li key={item.label}>
                  <a href={item.href} {...externalProps}>
                    {item.label}
                    <NewTabNote />
                  </a>
                </li>
              ))}
            </ul>
          </nav>

          <a className="btn btn-danger site-header__login" href={`${SITE}/account/`} {...externalProps}>
            Log in
            <NewTabNote />
          </a>

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

        <nav
          id="mobile-nav"
          className="mobile-nav"
          aria-label="Ohio Power Company mobile"
          hidden={!menuOpen}
        >
          <div className="container">
            <ul className="mobile-nav__list">
              {NAV.map((item) => (
                <li key={item.label}>
                  <a href={item.href} {...externalProps}>
                    {item.label}
                    <NewTabNote />
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </nav>
      </div>
    </header>
  );
}
