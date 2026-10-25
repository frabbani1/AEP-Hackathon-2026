/**
 * Plain-language explanations shown in the circled "i" help bubbles (see `components/InfoTip.tsx`).
 *
 * Write for a customer, not an engineer: short sentences, no jargon, and never a number the engine did not produce.
 * Each entry says what a thing is, or how it is worked out, and what it is not.
 */

/** The labels that say where a piece of information comes from. */
export const TAG_HELP = {
  model:
    "Our model's best guess, worked out from damage in past storms and the forecast. It is not confirmed by the utility.",
  confirmed:
    "Facts reported by the utility itself, such as a confirmed outage or a restoration time. Nothing in this section is guessed.",
  actions: "Things you can do yourself to get ready. They are suggestions, not predictions.",
  rules: "Decided by fixed, written rules that give a yes or no. It is not a prediction.",
  forecast:
    "Based on the weather forecast alone. There are no outage records for this place, so there is no outage risk or restoration time.",
} as const;

export const STORM_SUMMARY_HELP = "A short look at the storm situation: the weather, any outages, and what to do about it.";
export const SAMPLE_DATA_HELP = "This is placeholder text for now. It is not a real outage report, so don't rely on it.";

export const LOCATION_HELP =
  "A city or ZIP code is enough to find your nearest area. Please don't type a street address. We don't need it and don't keep it.";
export const LOCATION_CHIP_HELP =
  "Your city or ZIP code was matched to the closest map area that has outage records. The answers are for that area as a whole, not for your home.";

export const AREA_HELP =
  "An area is a group of many homes, named for a nearby community. We rate the whole area, never a single home, so no one's address can be picked out.";
export const FIND_AREA_HELP =
  "Type a city or ZIP code and we'll jump to the nearest area. If we have no outage records for that place, you'll get a forecast-only outlook instead.";

export const STORM_STRENGTH_HELP =
  "How strong a storm the answers assume. Live weather uses today's forecast wind gusts. What-if storm lets you pick a past storm to see how the answers change. A what-if is not a forecast.";
export const LEGEND_HELP =
  "Each area is ranked low, medium or high for losing power in the storm shown. The ranking compares areas with each other. It is not a percent chance. Gray means there is not enough past data to rate the area.";

export const WEATHER_GUST_HELP =
  "The strongest short burst of wind expected in the next 48 hours. Gusts matter more than steady wind because they are what usually bring down branches and lines.";
export const STORM_MULTIPLIER_HELP =
  "Compares the expected storm with a typical past storm, using the strongest wind gust. 1.00\u00d7 means about the same, lower is milder, and higher is stronger.";

export const RISK_HELP =
  "Shows how this area compares with others in the storm shown. Low, Medium and High is a ranking, not a percent chance. The area is rated through one typical home in it.";
export const CONFIDENCE_HELP =
  "How much past evidence backs this rating. More damage reports from past storms on the power line that serves this area means higher confidence. Few reports, or a storm stronger than any we have seen, lowers it.";
export const FACTORS_HELP =
  "The biggest things pushing the rating up or down. A longer bar means that factor has a bigger share of the effect.";

export const RESTORATION_HELP =
  "A range, not a promise. We play out the repairs many times with some luck both ways. The low end is the typical result (half of those runs finish by then). The high end is the cautious result (9 in 10 finish by then).";
export const STEPS_HELP =
  "Crews fix the parts closest to the power source first, because that brings back the most homes at once. Each time is when that stage is likely to be done in the typical result.";
export const MOVERS_HELP =
  "Things that could make repairs finish sooner or take longer. \u201cUnknown\u201d means we don't have that information yet.";
export const WHATIF_HELP =
  "Change a setting and the estimate updates. This only changes what you see here. It doesn't change anything about the real storm or the real crews.";
export const CREWS_HELP = "A crew is a team that repairs power lines. More crews working on this area usually means repairs finish sooner.";
export const ACCESS_HELP =
  "How easy it is for crews to reach the damage. Blocked roads or hard-to-reach equipment slow repairs down.";
export const REPAIRS_HELP =
  "A repair job is one fix, such as a broken pole or a blown fuse. We start with a typical number for this area and storm. Enter your own to see how it changes the estimate.";

export const ALERT_LEVEL_HELP =
  "A prototype setting for how soon and how often we would reach out: info, standard, elevated or priority. Higher levels mean sooner or more often. It is a demo, not a service promise.";
export const ALERT_RULES_HELP =
  "Alerts follow simple written rules. A check mark means that rule applies to this area right now.";

export const MESSAGES_HELP =
  "Previews of the messages you would get at each stage of a storm if you signed up for alerts. Nothing is being sent now.";

export const EVIDENCE_HELP =
  "A backtest replays the model on past storms and checks whether areas it rated higher really had more damage. It shows how well the model did, and where it fell short.";

export const OUTLOOK_LEVEL_HELP =
  "A four-step scale (quiet, watch, elevated, high) based only on the strongest wind gust in the forecast. It doesn't use outage history, so it isn't an outage risk rating.";
