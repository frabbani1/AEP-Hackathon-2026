import { internalLink, type RoutePath } from "../lib/useRoute";
import "./chrome.css";

/** Every link stays inside this app. */
const LINKS: ReadonlyArray<{ label: string; to: RoutePath }> = [
  { label: "Ask the Concierge", to: "/" },
  { label: "Outage map", to: "/map" },
  { label: "Safety", to: "/safety" },
  { label: "About", to: "/about" },
];

interface Props {
  onNavigate: (to: RoutePath) => void;
}

export function SiteFooter({ onNavigate }: Props) {
  return (
    <footer className="site-footer">
      <div className="site-footer__band">
        <div className="container">
          <ul className="site-footer__links">
            {LINKS.map((link) => (
              <li key={link.to}>
                <a href={link.to} onClick={internalLink(onNavigate, link.to)}>
                  {link.label}
                </a>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div className="container site-footer__sub">
        <p>
          <span className="site-footer__help">Need help? Call us.</span>{" "}
          <a href="tel:8006722231">800-672-2231</a>
        </p>
        <p className="site-footer__legal">
          HackOHI/O 2026 prototype. Not an official Ohio Power Company service.
        </p>
      </div>
    </footer>
  );
}
