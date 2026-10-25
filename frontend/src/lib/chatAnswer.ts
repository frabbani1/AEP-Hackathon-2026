import type { WeatherOutlook, ZoneAssessment } from "../api";
import type { Intent } from "./chatIntent";
import { BAND_LABEL } from "./bands";
import { SEVERITY_STEPS } from "./scenario";

/**
 * Chat replies built from the same engine answers the Outage map shows. This file only arranges text and numbers the
 * API already produced: it never computes, rounds or invents a figure. Safety wording matches the assistant's own
 * (`backend/concierge/assistant/safety.py`).
 */

export const DOWNED_LINES = "Stay far away from downed power lines. Assume they are live, keep others away, and call 911.";
export const GENERATOR_CO = "Never run a generator, grill or camp stove inside your home or garage. Carbon monoxide can kill.";
export const EMERGENCY = "In an emergency, call 911. Utility alerts and this tool do not replace emergency services.";
const SHORT_911 = "If you see a downed power line, stay far away and call 911.";
const ESTIMATE_NOTE =
  "These are model estimates from past storms, not confirmed utility information. Open the Outage map page to try other storms and what-if changes.";
const PHONE = "800-672-2231";

const PREP_ITEMS_SHOWN = 4;
const FACTORS_SHOWN = 3;

/** The storm the answer was computed for, in plain words. */
function basis(a: ZoneAssessment): string {
  if (a.severity.preset === "weather") return "using the live weather forecast for this area";
  const step = SEVERITY_STEPS.find((s) => s.id === a.severity.preset);
  return step ? `for the "${step.label}" scenario` : "for a custom storm strength";
}

const list = (items: string[]) => items.map((item) => `- ${item}`).join("\n");

function riskText(a: ZoneAssessment): string[] {
  if (!a.risk) return [];
  const why = a.risk.topFactors.slice(0, FACTORS_SHOWN).map((factor) => factor.text);
  return [
    `${a.zoneName}: ${BAND_LABEL[a.risk.band]} of losing power, ${basis(a)}. Confidence in this rating: ${a.risk.confidence.level}.`,
    ...(why.length > 0 ? [`What drives this rating:\n${list(why)}`] : []),
    a.risk.scopeNote,
  ];
}

function restorationText(a: ZoneAssessment): string[] {
  if (!a.restoration) return [];
  return [
    `If power does go out in ${a.zoneName}, restoration could take ${a.restoration.rangeText} (${a.restoration.label}).`,
    a.restoration.meaning,
  ];
}

function prepareText(a: ZoneAssessment): string[] {
  const out: string[] = [];
  const before = a.preparation.groups.find((group) => group.id === "before");
  if (before) {
    out.push(`Before the storm:\n${list(before.actions.slice(0, PREP_ITEMS_SHOWN).map((action) => action.text))}`);
  }
  if (a.preparation.safety.length > 0) {
    out.push(`Safety first:\n${list(a.preparation.safety.map((notice) => notice.text))}`);
  }
  out.push("The Outage map page has the full checklist for before, during and after an outage.");
  return out;
}

function alertsText(a: ZoneAssessment): string[] {
  const out = [a.alerts.summary];
  if (a.alerts.eligible && a.alerts.timing) out.push(a.alerts.timing);
  if (a.alerts.eligible && a.alerts.channels.length > 0) out.push(`Channels: ${a.alerts.channels.join(", ")}.`);
  if (a.alerts.note) out.push(a.alerts.note);
  return out;
}

/** Reply for a question about one area. `a` is the full assessment the Outage map would show. */
export function answerFor(intent: Intent, a: ZoneAssessment): string {
  if (a.status !== "ok" || !a.risk) {
    const reason = a.fallback?.message ?? "There isn't enough storm history here to rate this area.";
    return [
      `${a.zoneName}: ${reason}`,
      ...(intent === "alerts" ? alertsText(a) : []),
      `The safety steps still apply. A person can also help: ${PHONE}.`,
      SHORT_911,
    ].join("\n\n");
  }

  let body: string[];
  switch (intent) {
    case "risk":
      body = riskText(a);
      break;
    case "restoration":
      body = restorationText(a);
      break;
    case "prepare":
      body = prepareText(a);
      break;
    case "alerts":
      body = alertsText(a);
      break;
    default: {
      const [headline] = riskText(a);
      const [range] = restorationText(a);
      body = [headline, range, "Ask me how to prepare or whether you qualify for alerts."].filter(Boolean);
    }
  }
  return [...body, ESTIMATE_NOTE, ...(intent === "prepare" ? [] : [SHORT_911])].join("\n\n");
}

/**
 * Reply for a place with no outage data. Built from the forecast-only outlook the API returned. It says up front that
 * this is a prediction, and says what it cannot give, so it never reads like an outage rating.
 */
export function outlookAnswer(o: WeatherOutlook): string {
  const name = o.place.name;
  return [
    `I don't have outage data for ${name}, so I can't rate your outage risk or estimate restoration. What I can share is a prediction from the weather forecast.`,
    `${o.outlook.label} weather level. ${o.outlook.summary}`,
    ...(o.hazards.length > 0 ? [list(o.hazards.map((hazard) => hazard.text))] : []),
    `How to get ready:\n${list(o.preparation.actions.slice(0, PREP_ITEMS_SHOWN).map((action) => action.text))}`,
    `Not available for ${name}: ${o.notAvailable.map((item) => item.toLowerCase()).join(", ")}.`,
    SHORT_911,
  ].join("\n\n");
}

/** Safety questions need no location, so they get the fixed safety text straight away. */
export function safetyAnswer(): string {
  return [
    "Safety comes first.",
    list([DOWNED_LINES, GENERATOR_CO, EMERGENCY]),
    "To report an outage, use your utility's official outage line or app.",
  ].join("\n\n");
}
