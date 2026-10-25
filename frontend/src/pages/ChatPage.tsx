import { useEffect, useRef } from "react";
import { conciergeClient } from "../api";
import { ChatThread } from "../components/ChatThread";
import { Composer } from "../components/Composer";
import { LocationScope } from "../components/LocationScope";
import { StormSummary } from "../components/StormSummary";
import { useEventSummary } from "../hooks/useEventSummary";
import type { useChat } from "../hooks/useChat";

/** The chat home page. Conversation state lives in App so it survives a trip to the map. */
export function ChatPage({ chat }: { chat: ReturnType<typeof useChat> }) {
  const { messages, location, area, pending, send, setLocation } = chat;
  const summary = useEventSummary(conciergeClient, location);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  // Keep the newest message in view. The composer is sticky, so scroll the page itself.
  // Messages that were already there when the page opened do not trigger a scroll.
  const messageCount = messages.filter((message) => message.role !== "notice").length;
  const seenCount = useRef(messageCount);
  useEffect(() => {
    if (messageCount === seenCount.current && !pending) return;
    seenCount.current = messageCount;
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
    <>
      <StormSummary state={summary} />
      <LocationScope location={location} areaName={area?.zoneName ?? null} onChange={setLocation} />
      <main id="main" className="app__main" tabIndex={-1}>
        <ChatThread messages={messages} pending={pending} onPrompt={handlePrompt} />
        <Composer location={location} pending={pending} inputRef={composerRef} onSend={(text) => void send(text)} />
      </main>
    </>
  );
}
