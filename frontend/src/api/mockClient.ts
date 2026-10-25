import type { AskRequest, AssistantReply, ConciergeClient, EventSummary } from "./types";

/**
 * Stand-in for the chat half of the Concierge API, which has no backend yet.
 *
 * The chat methods return static, number-free placeholder content. They deliberately
 * do NOT invent risk levels, restoration times or alert eligibility. The map is live:
 * see `httpClient.ts`.
 *
 * Why the chat is not wired: a typed city or ZIP cannot be turned into an area, because
 * nothing in the data maps either to a map zone, and the engines take only opaque ids.
 */

const LATENCY_MS = 600;

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

export const mockClient: Pick<ConciergeClient, "getEventSummary" | "ask"> = {
  async getEventSummary(location, signal): Promise<EventSummary> {
    await wait(250, signal);
    const headline = location
      ? `Severe storms are in the forecast in ${location}. Damaging wind could cause outages. Ask me what it could mean for you.`
      : "Severe storms are in the forecast in your area. Damaging wind could cause outages. Tell me your location and I will help you plan.";
    return {
      source: "sample",
      headline,
      events: [
        {
          id: "storm",
          kind: "storm",
          title: "Storm watch",
          detail: "Thunderstorms with strong wind and lightning possible.",
        },
        {
          id: "outage",
          kind: "outage",
          title: "Outage activity",
          detail: "Live outage counts will appear here once the data feed is connected.",
        },
        {
          id: "restoration",
          kind: "restoration",
          title: "Restoration",
          detail: "Estimated ranges are model output, not confirmed times.",
        },
        {
          id: "safety",
          kind: "safety",
          title: "Safety first",
          detail: "Treat every downed line as energized. Stay back and call 911.",
        },
      ],
    };
  },

  async ask({ location }: AskRequest, signal): Promise<AssistantReply> {
    await wait(LATENCY_MS, signal);

    if (!location) {
      return {
        isPreview: true,
        content:
          "I can help with that. First, tell me the city or ZIP code you want to look at using the location box above.\n\n" +
          "You never need to share your street address or account number. The Outage map page already shows live model answers by area.",
      };
    }

    return {
      isPreview: true,
      content:
        `Thanks. I am scoped to ${location}.\n\n` +
        "This chat is not connected to the outage risk engine yet, so I can't tell you how likely an outage is or how long restoration could take. " +
        "For real model answers, open the Outage map page: pick an area and you will see a risk level with how confident the model is, a restoration time range, steps to prepare, and whether you qualify for alerts.\n\n" +
        "If you see a downed power line, stay far away and call 911.",
    };
  },
};
