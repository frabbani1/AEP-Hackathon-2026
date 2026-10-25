import type { ConciergeClient, EventSummary } from "./types";

/**
 * Stand-in for the storm summary on the chat page, which has no backend yet.
 *
 * It returns static, number-free placeholder content and is labeled "Sample data" in the UI. It deliberately does NOT
 * claim a forecast, and does not invent risk levels, restoration times or alert eligibility. The chat answers and the
 * map are live: see `httpClient.ts`.
 */

const LATENCY_MS = 250;

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

export const mockClient: Pick<ConciergeClient, "getEventSummary"> = {
  async getEventSummary(location, signal): Promise<EventSummary> {
    await wait(LATENCY_MS, signal);
    const headline = location
      ? `Planning ahead for storms in ${location}. Ask me what a storm could mean for you.`
      : "Planning ahead for storms. Tell me your city or ZIP code and I will help you plan.";
    return {
      source: "sample",
      headline,
      events: [
        {
          id: "storm",
          kind: "storm",
          title: "Storm watch",
          detail: "Live storm information is not connected to this summary yet. The Outage map page uses a live forecast.",
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
};
