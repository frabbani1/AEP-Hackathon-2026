import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessage, ConciergeClient } from "../api";

let counter = 0;
const nextId = () => `m${++counter}`;

const ERROR_TEXT =
  "Sorry, I couldn't get an answer just now. Please try again in a moment. " +
  "If this is an emergency, such as a downed line, call 911.";

export function useChat(client: ConciergeClient) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [location, setLocationState] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  // Cancel any in-flight request when the page unmounts.
  useEffect(() => () => controllerRef.current?.abort(), []);

  const setLocation = useCallback((next: string | null) => {
    setLocationState(next);
    setMessages((prev) => [
      ...prev,
      {
        id: nextId(),
        role: "notice",
        content: next ? `Location set to ${next}` : "Location cleared",
      },
    ]);
  }, []);

  const send = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || pending) return;

      const controller = new AbortController();
      controllerRef.current = controller;

      setMessages((prev) => [...prev, { id: nextId(), role: "user", content: question }]);
      setPending(true);

      try {
        const reply = await client.ask({ question, location }, controller.signal);
        setMessages((prev) => [
          ...prev,
          { id: nextId(), role: "assistant", content: reply.content, isPreview: reply.isPreview },
        ]);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setMessages((prev) => [
          ...prev,
          { id: nextId(), role: "assistant", content: ERROR_TEXT, isError: true },
        ]);
      } finally {
        if (controllerRef.current === controller) {
          controllerRef.current = null;
          setPending(false);
        }
      }
    },
    [client, location, pending],
  );

  return { messages, location, pending, send, setLocation };
}
