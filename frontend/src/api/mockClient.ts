import { sampleAssessment, sampleMapZones } from "./sample/assessment";
import type { AskRequest, AssistantReply, ConciergeClient, EventSummary } from "./types";

/**
 * Stand-in for the Concierge API, which is not built yet.
 *
 * The chat methods return static, number-free placeholder content. They deliberately
 * do NOT invent risk levels, restoration times or alert eligibility.
 *
 * The map methods are the exception: the map page needs something to color and
 * recompute, so they return clearly labeled `source: "sample"` data from
 * `./sample/assessment`. That data is placeholder only and must never be shown as a
 * real prediction. The real numbers must come from the deterministic engine.
 */

const LATENCY_MS = 600;
const MAP_LATENCY_MS = 350;
const ASSESSMENT_LATENCY_MS = 450;

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

export const mockClient: ConciergeClient = {
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

  async getMapZones({ severity }, signal) {
    await wait(MAP_LATENCY_MS, signal);
    return sampleMapZones(severity);
  },

  async getZoneAssessment(request, signal) {
    await wait(ASSESSMENT_LATENCY_MS, signal);
    const assessment = sampleAssessment(request);
    if (!assessment) throw new Error("Unknown area");
    return assessment;
  },

  async ask({ location }: AskRequest, signal): Promise<AssistantReply> {
    await wait(LATENCY_MS, signal);

    if (!location) {
      return {
        isPreview: true,
        content:
          "I can help with that. First, tell me the city or ZIP code you want to look at using the location box above.\n\n" +
          "You never need to share your street address or account number.",
      };
    }

    return {
      isPreview: true,
      content:
        `Thanks. I am scoped to ${location}.\n\n` +
        "This preview is not connected to the outage risk engine yet, so I can't tell you how likely an outage is or how long restoration could take. " +
        "Once it is connected, I will show a risk level with how confident the model is, a restoration time range, steps to prepare, and whether you qualify for alerts.\n\n" +
        "If you see a downed power line, stay far away and call 911.",
    };
  },
};
