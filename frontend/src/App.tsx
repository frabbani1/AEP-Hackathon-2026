import { useEffect, useRef } from "react";
import { conciergeClient } from "./api";
import { AppNav } from "./components/AppNav";
import { SiteFooter } from "./components/SiteFooter";
import { SiteHeader } from "./components/SiteHeader";
import { useChat } from "./hooks/useChat";
import { useRoute, type RoutePath } from "./lib/useRoute";
import { ChatPage } from "./pages/ChatPage";
import { MapPage } from "./pages/MapPage";
import "./App.css";

const TITLES: Record<RoutePath, string> = {
  "/": "Outage Concierge | Ohio Power Company",
  "/map": "Outage map | Outage Concierge",
};

export default function App() {
  const { path, navigate } = useRoute();
  // Chat state lives here so the conversation and location survive a visit to the map.
  const chat = useChat(conciergeClient);

  // On each page change: set the title, and move to the top and into the new page's main
  // landmark so keyboard and screen reader users start in the right place.
  const shownPath = useRef(path);
  useEffect(() => {
    document.title = TITLES[path];
    if (shownPath.current === path) return;
    shownPath.current = path;
    window.scrollTo(0, 0);
    document.getElementById("main")?.focus({ preventScroll: true });
  }, [path]);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <SiteHeader />
      <AppNav path={path} onNavigate={navigate} />
      {path === "/map" ? <MapPage /> : <ChatPage chat={chat} />}
      <SiteFooter />
    </div>
  );
}
