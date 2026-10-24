/**
 * Contract between the chat UI and the (not yet built) Concierge API.
 *
 * Keep the UI talking to `ConciergeClient` only. When the real API exists, add an
 * HTTP implementation next to `mockClient.ts` and swap it in `api/index.ts`.
 *
 * Privacy: never put street addresses, account numbers, premise IDs or other
 * internal identifiers into these types. Location is a city or ZIP the customer typed.
 */

export type EventKind = "storm" | "outage" | "restoration" | "safety";

export interface StormEvent {
  id: string;
  kind: EventKind;
  title: string;
  detail: string;
}

export interface EventSummary {
  headline: string;
  events: StormEvent[];
  /** "sample" means placeholder content, not confirmed utility information. */
  source: "sample" | "live";
}

export type ChatRole = "user" | "assistant" | "notice";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  /** Assistant reply produced without live data. Rendered with a "Preview" label. */
  isPreview?: boolean;
  isError?: boolean;
}

export interface AskRequest {
  question: string;
  /** City or ZIP the customer scoped the conversation to, if any. */
  location: string | null;
}

export interface AssistantReply {
  content: string;
  isPreview: boolean;
}

export interface ConciergeClient {
  getEventSummary(location: string | null, signal?: AbortSignal): Promise<EventSummary>;
  ask(request: AskRequest, signal?: AbortSignal): Promise<AssistantReply>;
}
