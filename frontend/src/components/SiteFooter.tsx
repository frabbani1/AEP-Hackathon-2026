import "./chrome.css";

const SITE = "https://www.aepohio.com";

const LINKS = [
  { label: "Safety", href: `${SITE}/safety/` },
  { label: "Outages", href: `${SITE}/outages/` },
  { label: "Contact", href: `${SITE}/contact` },
];

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="site-footer__band">
        <div className="container">
          <ul className="site-footer__links">
            {LINKS.map((link) => (
              <li key={link.label}>
                <a href={link.href} target="_blank" rel="noopener noreferrer">
                  {link.label}
                  <span className="sr-only"> (opens in a new tab)</span>
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
