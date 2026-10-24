import { useEffect, useRef } from "react";
import { conciergeClient } from "./api";
import { ChatThread } from "./components/ChatThread";
import { Composer } from "./components/Composer";
import { LocationScope } from "./components/LocationScope";
import { SiteFooter } from "./components/SiteFooter";
import { SiteHeader } from "./components/SiteHeader";
import { StormSummary } from "./components/StormSummary";
import { useChat } from "./hooks/useChat";
import { useEventSummary } from "./hooks/useEventSummary";
import "./App.css";

export default function App() {
  const { messages, location, pending, send, setLocation } = useChat(conciergeClient);
  const summary = useEventSummary(conciergeClient, location);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  // Keep the newest message in view. The composer is sticky, so scroll the page itself.
  const messageCount = messages.filter((message) => message.role !== "notice").length;
  useEffect(() => {
    if (messageCount === 0 && !pending) return;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({
      top: document.documentElement.scrollHeight,
      behavior: reduceMotion ? "auto" : "smooth",
    });
  }, [messageCount, pending]);

  const handlePrompt = (question: string) => {
    void send(question);
    composerRef.current?.focus();
  };

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to chat
      </a>
      <SiteHeader />
      <StormSummary state={summary} />
      <LocationScope location={location} onChange={setLocation} />
      <main id="main" className="app__main" tabIndex={-1}>
        <ChatThread messages={messages} pending={pending} onPrompt={handlePrompt} />
        <Composer location={location} pending={pending} inputRef={composerRef} onSend={(text) => void send(text)} />
      </main>
      <SiteFooter />
    </div>
  );
}
