import { useCallback, useEffect, useRef, useState } from "react";
import type { AreaRef, ChatMessage, ConciergeClient, ZoneAssessment } from "../api";
import { ApiError } from "../api/httpClient";
import { answerFor, outlookAnswer, safetyAnswer } from "../lib/chatAnswer";
import { detectIntent, extractLocation, type Intent } from "../lib/chatIntent";
import { parseLocation } from "../lib/parseLocation";
import { DEFAULT_SCENARIO } from "../lib/scenario";

let counter = 0;
const nextId = () => `m${++counter}`;

const ERROR_TEXT =
  "Sorry, I couldn't get an answer just now. Please try again in a moment. " +
  "If this is an emergency, such as a downed line, call 911.";

/** An answer is reused for the rest of the conversation for this long. The forecast behind it moves slowly. */
const ANSWER_TTL_MS = 10 * 60 * 1000;

const HELP_TEXT =
  "I can tell you how likely an outage is, how long restoration could take, how to prepare, and whether you qualify for alerts. " +
  "Try one of the questions above, or ask in your own words.";

/** `isForecast`: the message is a forecast-only outlook for a place with no outage data, not just an explanation. */
type Resolved =
  | { ok: true; area: AreaRef; exact: boolean }
  | { ok: false; message: string; isForecast?: boolean };

const isAbort = (error: unknown) => error instanceof DOMException && error.name === "AbortError";

function listPlaces(places: string[]): string {
  if (places.length === 0) return "the Roanoke Valley area";
  if (places.length === 1) return places[0];
  return `${places.slice(0, -1).join(", ")} and ${places[places.length - 1]}`;
}

/**
 * Chat state. The customer gives a city or ZIP code (in the location box or in a message), it is matched to a map area
 * on the server, and questions are then answered from that area's engine results. A question asked before a location is
 * remembered and answered as soon as one arrives, so the conversation never goes back to the same question.
 */
export function useChat(client: ConciergeClient) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [location, setLocationState] = useState<string | null>(null);
  const [area, setAreaState] = useState<AreaRef | null>(null);
  const [pending, setPending] = useState(false);

  const areaRef = useRef<AreaRef | null>(null);
  const wantedRef = useRef<Intent | null>(null); // a question waiting for a location
  const askedRef = useRef(0); // how many times we have asked for a location in a row
  const busyRef = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);
  const answerRef = useRef<{ zoneId: string; at: number; data: ZoneAssessment } | null>(null);

  // Cancel any in-flight request when the page unmounts.
  useEffect(() => () => controllerRef.current?.abort(), []);

  const add = useCallback((message: Omit<ChatMessage, "id">) => {
    setMessages((prev) => [...prev, { id: nextId(), ...message }]);
  }, []);

  const assessmentFor = useCallback(
    async (target: AreaRef, signal: AbortSignal): Promise<ZoneAssessment> => {
      const cached = answerRef.current;
      if (cached && cached.zoneId === target.zoneId && Date.now() - cached.at < ANSWER_TTL_MS) return cached.data;
      const data = await client.getZoneAssessment(
        { zoneId: target.zoneId, scenario: DEFAULT_SCENARIO, declaredNeeds: [] },
        signal,
      );
      answerRef.current = { zoneId: target.zoneId, at: Date.now(), data };
      return data;
    },
    [client],
  );

  /** Match typed text to an area. Sets the location only when it matched; never posts messages itself. */
  const resolve = useCallback(
    async (query: string, signal: AbortSignal): Promise<Resolved> => {
      const parsed = parseLocation(query);
      if (!parsed.ok) return { ok: false, message: parsed.error };
      const result = await client.findArea(parsed.value, signal);
      if (result.status === "not_covered") {
        // No outage data here. Offer what the weather forecast can say, clearly labeled as a prediction. If that
        // fails too, fall back to explaining the coverage.
        try {
          const outlook = await client.getOutlook(parsed.value, signal);
          if (outlook.status === "ok") return { ok: false, message: outlookAnswer(outlook), isForecast: true };
        } catch (error) {
          if (isAbort(error)) throw error;
        }
        return {
          ok: false,
          message:
            `I don't have outage data for ${parsed.value} yet. The data behind this prototype covers ` +
            `${listPlaces(result.coveredPlaces)} and the communities around them. Try one of those, or a ZIP code there.`,
        };
      }
      const found: AreaRef = { zoneId: result.zoneId, zoneName: result.zoneName };
      areaRef.current = found;
      setAreaState(found);
      setLocationState(parsed.value);
      return { ok: true, area: found, exact: result.exact };
    },
    [client],
  );

  const announce = useCallback(
    (value: string, found: AreaRef, exact: boolean) => {
      add({
        role: "notice",
        content: exact
          ? `Location set to ${value}. Showing answers for the ${found.zoneName} area.`
          : `Location set to ${value}. The closest area I have is ${found.zoneName}, so answers are for that area.`,
      });
    },
    [add],
  );

  const answer = useCallback(
    async (intent: Intent, found: AreaRef, signal: AbortSignal) => {
      const assessment = await assessmentFor(found, signal);
      add({ role: "assistant", content: answerFor(intent, assessment), isEstimate: true });
    },
    [add, assessmentFor],
  );

  const begin = useCallback(() => {
    const controller = new AbortController();
    controllerRef.current = controller;
    busyRef.current = true;
    setPending(true);
    return controller;
  }, []);

  const finish = useCallback(() => {
    controllerRef.current = null;
    busyRef.current = false;
    setPending(false);
  }, []);

  const failureText = (error: unknown) => (error instanceof ApiError && error.status === 400 ? error.message : ERROR_TEXT);

  const send = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || busyRef.current) return;

      const controller = begin();
      add({ role: "user", content: question });

      try {
        const intent = detectIntent(question);
        const place = extractLocation(question);

        // Safety questions need no location and get the fixed safety text.
        if (intent === "safety") {
          add({ role: "assistant", content: safetyAnswer() });
          return;
        }

        let current = areaRef.current;
        if (place) {
          const resolved = await resolve(place, controller.signal);
          if (!resolved.ok) {
            if (intent) wantedRef.current = intent;
            add({ role: "assistant", content: resolved.message, isForecast: resolved.isForecast });
            return;
          }
          askedRef.current = 0;
          current = resolved.area;
          announce(place, resolved.area, resolved.exact);
        }

        const wanted: Intent | null = intent ?? (place ? wantedRef.current : null);

        if (!current) {
          if (wanted) wantedRef.current = wanted;
          askedRef.current += 1;
          add({
            role: "assistant",
            content:
              askedRef.current === 1
                ? "I can help with that. Which city or ZIP code are you in? Type it here or use the location box above.\n\n" +
                  "You never need to share your street address or account number."
                : "I still need a city or ZIP code to look up your area, for example Roanoke or 24019. " +
                  "Type just the city or ZIP code and I will answer your question right away.",
          });
          return;
        }

        if (!wanted) {
          add({ role: "assistant", content: `I'm looking at the ${current.zoneName} area. ${HELP_TEXT}` });
          return;
        }

        wantedRef.current = null;
        await answer(wanted, current, controller.signal);
      } catch (error) {
        if (isAbort(error)) return;
        add({ role: "assistant", content: failureText(error), isError: true });
      } finally {
        finish();
      }
    },
    [add, announce, answer, begin, finish, resolve],
  );

  /**
   * Set or clear the location from the location box. Resolves to a message to show beside the box when the location
   * could not be used, or null when it worked.
   */
  const setLocation = useCallback(
    async (next: string | null): Promise<string | null> => {
      if (next === null) {
        controllerRef.current?.abort();
        areaRef.current = null;
        answerRef.current = null;
        wantedRef.current = null;
        askedRef.current = 0;
        setAreaState(null);
        setLocationState(null);
        add({ role: "notice", content: "Location cleared" });
        return null;
      }
      if (busyRef.current) return "One moment, I'm still working on your last question.";

      const controller = begin();
      try {
        const resolved = await resolve(next, controller.signal);
        if (!resolved.ok) {
          if (!resolved.isForecast) return resolved.message;
          add({ role: "assistant", content: resolved.message, isForecast: true });
          return "No outage data for that place. A forecast-only outlook is in the chat.";
        }
        askedRef.current = 0;
        announce(next, resolved.area, resolved.exact);
        const wanted = wantedRef.current;
        if (wanted) {
          wantedRef.current = null;
          try {
            await answer(wanted, resolved.area, controller.signal);
          } catch (error) {
            if (!isAbort(error)) add({ role: "assistant", content: failureText(error), isError: true });
          }
        }
        return null;
      } catch (error) {
        if (isAbort(error)) return null;
        return failureText(error);
      } finally {
        finish();
      }
    },
    [add, announce, answer, begin, finish, resolve],
  );

  return { messages, location, area, pending, send, setLocation };
}
