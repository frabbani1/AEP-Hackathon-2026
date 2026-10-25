import { parseLocation } from "./parseLocation";

/** What a chat message is asking about. "overview" means a general question: give the short version of everything. */
export type Intent = "alerts" | "restoration" | "prepare" | "risk" | "safety" | "overview";

/**
 * The checks run in this order because the four starter questions overlap: "Do I qualify for outage alerts?" also
 * says "outage", and "How long could restoration take?" also says "could".
 */
const INTENTS: ReadonlyArray<[Intent, RegExp]> = [
  ["safety", /\b(downed|fallen|sparking|sparks|fire|gas leak|smell gas|carbon monoxide|generator|911|emergency|electrocut)/i],
  ["alerts", /\b(alerts?|notify|notifications?|notified|qualif\w*|eligib\w*|sign ?up|text me|warn(?:ing)?s?)\b/i],
  [
    "restoration",
    /\b(restor\w*|how long|come back|be back|back on|get (?:my )?power back|repair\w*|when will|how many hours)\b/i,
  ],
  ["prepare", /\b(prepar\w*|get ready|ready for|checklist|what (?:should|can) i do|before the storm|supplies|kit)\b/i],
  ["risk", /\b(lose power|loose power|outages?|risk|likely|likelihood|chance|go out|power (?:go|loss)|will i)\b/i],
];

/** The intent of a question, or null when it does not look like a question about outages. */
export function detectIntent(text: string): Intent | null {
  for (const [intent, pattern] of INTENTS) if (pattern.test(text)) return intent;
  const trimmed = text.trim();
  return /\?$/.test(trimmed) || /^(?:what|how|will|can|could|do|does|is|are|should|when|where|why)\b/i.test(trimmed)
    ? "overview"
    : null;
}

const ZIP_ANYWHERE = /(?<![\d-])(\d{5})(?:-\d{4})?(?![\d-])/;
/** "in Salem", "near Roanoke, VA", "I live in Cave Spring". Captures the place at the end of the message. */
const PLACE_AT_END = /\b(?:in|near|around|at|for|from|about)\s+([\p{L}][\p{L} .'’-]{1,38}?(?:,\s*[A-Za-z]{2}|,\s*virginia)?)\s*[?.!]*$/iu;
const LEAD_IN = /^(?:i(?:'m| am| live| work)?\s+(?:in|near|at)|my (?:city|town|zip(?: code)?|location) is|it(?:'s| is)|we(?:'re| are) in|location:?)\s+/i;
const MAX_WORDS = 4;
/** Short replies that are not places. */
const NOT_A_PLACE = /^(?:yes|no|ok|okay|sure|yep|nope|thanks?|thank you|hi|hello|hey|help|please|test|none|nothing|why|who|what)$/i;

/**
 * Pulls a city or ZIP code out of a chat message, or returns null. A ZIP code anywhere in the message counts, so does
 * "in Salem" at the end of one, and so does a short message that is nothing but a place ("Salem", "Cave Spring, VA").
 * A message that reads like a question about outages is never taken for a bare place name.
 */
export function extractLocation(text: string): string | null {
  const message = text.trim().replace(/\s+/g, " ");
  const zip = ZIP_ANYWHERE.exec(message);
  if (zip) return zip[1];

  const bare = message.replace(LEAD_IN, "").replace(/[.!]+$/, "");
  const words = bare.split(" ").length;
  if (words <= MAX_WORDS && !NOT_A_PLACE.test(bare) && detectIntent(bare) === null && parseLocation(bare).ok) {
    return bare;
  }

  const tail = PLACE_AT_END.exec(message);
  const candidate = tail?.[1].trim();
  // "for outage alerts" is not a place: a candidate that reads like a topic is skipped.
  if (candidate && detectIntent(candidate) === null && parseLocation(candidate).ok) return candidate;
  return null;
}
