import type {
  AlertEligibility,
  ConfidenceLevel,
  DamageKind,
  DeclaredNeed,
  MapZone,
  MapZonesResponse,
  PrepAction,
  PrepGroup,
  Preparation,
  ProactiveMessage,
  RestorationEstimate,
  RestorationFactor,
  RiskAssessment,
  RiskBand,
  RiskFactor,
  SafetyNotice,
  SeverityInfo,
  SeverityPreset,
  ZoneAssessment,
  ZoneAssessmentRequest,
} from "../types";
import { MIN_HOMES_PER_AREA, SAMPLE_BOUNDS, SAMPLE_ZONES, type FallbackReason, type SampleZone } from "./zones";

/**
 * PLACEHOLDER STAND-IN FOR THE DETERMINISTIC ENGINES. NOT A REAL PREDICTION.
 *
 * It exists so the map page can be exercised end to end before the API is built. The
 * formulas below are deliberately simple and are not calibrated against the data. Only
 * these inputs are grounded in the repo:
 *  - the severity multipliers (the engine's presets: the two past storms had 193 and
 *    1,254 damage records, so "Sep 2024" is about 0.27 and "Feb 2025" about 1.73 of the mean)
 *  - the indirect labor multiplier of 4.5 from the labor workbook
 *  - the engine's confidence cut-offs (20 and 5 reports) and its fallback wording
 * Everything is labeled "sample" in the UI. The page components never compute numbers;
 * they render what the client returns, so this file can be deleted when the API exists.
 */

const SEVERITY_MULTIPLIER: Record<SeverityPreset, number> = {
  "sep-2024-like": 0.27,
  typical: 1.0,
  "feb-2025-like": 1.73,
  "beyond-observed": 2.5,
};
const MAX_OBSERVED_MULTIPLIER = 1.73;

const LEVELS: readonly ConfidenceLevel[] = ["Low", "Medium", "High"];
const REF_RATE_PER_1K = 8; // placeholder "typical line" damage rate
const RATE_PER_BASE = 9;
const DAMPING = 0.6; // placeholder: soften the multiplier so every preset shows a mix of bands
const INDIRECT_LABOR_MULTIPLIER = 4.5; // from Install_Labor_HackOHIO.xlsx
const ASSESSMENT_HOURS = 2; // placeholder time to inspect before repairs start

/** Placeholder direct crew-hours per repair, before the indirect multiplier. */
const CREW_HOURS: Record<DamageKind, number> = {
  pole: 3,
  transformer: 1.5,
  fuse: 0.5,
  primary: 2.5,
  secondary: 1,
};
const DAMAGE_PER_BASE: Record<DamageKind, number> = {
  pole: 1.2,
  fuse: 2,
  primary: 1.5,
  transformer: 0.6,
  secondary: 3,
};

const FALLBACK_MESSAGES: Record<FallbackReason, string> = {
  circuit_too_small:
    "The power line serving this area has too few homes for us to share a rating without risking privacy. Prepare as if an outage is possible.",
  no_history_small_circuit:
    "We don't have enough storm history for the line serving this area to rate it. Prepare as if an outage is possible.",
};

/* ------------------------------ helpers ---------------------------------- */

const round1 = (value: number) => Math.round(value * 10) / 10;
const round2 = (value: number) => Math.round(value * 100) / 100;

function severityInfo(preset: SeverityPreset): SeverityInfo {
  const multiplier = SEVERITY_MULTIPLIER[preset];
  return { preset, multiplier, extrapolated: multiplier > MAX_OBSERVED_MULTIPLIER + 1e-9 };
}

function bandFor(zone: SampleZone, multiplier: number): RiskBand {
  const intensity = zone.base * Math.pow(multiplier, DAMPING);
  if (intensity < 0.5) return "Low";
  if (intensity < 0.85) return "Medium";
  return "High";
}

function confidenceFor(zone: SampleZone, extrapolated: boolean): ConfidenceLevel {
  let index = zone.records >= 20 ? 2 : zone.records >= 5 ? 1 : 0;
  if (extrapolated) index = Math.max(0, index - 1);
  return LEVELS[index];
}

function ratePer1k(zone: SampleZone): number {
  return round1(zone.base * RATE_PER_BASE);
}

const plural = (count: number, one: string, many: string) => (count === 1 ? one : many);

/* ------------------------------ map layer -------------------------------- */

export function sampleMapZones(preset: SeverityPreset): MapZonesResponse {
  const severity = severityInfo(preset);
  const zones: MapZone[] = SAMPLE_ZONES.map((zone) => {
    const rated = zone.fallback === null;
    return {
      id: zone.id,
      name: zone.name,
      ring: zone.ring,
      band: rated ? bandFor(zone, severity.multiplier) : null,
      confidence: rated ? confidenceFor(zone, severity.extrapolated) : null,
    };
  });
  return { source: "sample", severity, bounds: SAMPLE_BOUNDS, minHomesPerArea: MIN_HOMES_PER_AREA, zones };
}

/* --------------------------- risk explanation ---------------------------- */

function riskFactors(zone: SampleZone, multiplier: number): RiskFactor[] {
  const rate = ratePer1k(zone);
  const contributions: Array<{ id: string; value: number }> = [
    { id: "hist", value: Math.log(Math.max(rate, 0.1) / REF_RATE_PER_1K) },
    { id: "type", value: zone.service === "overhead" ? 0.3 : zone.service === "underground" ? -0.6 : 0 },
    { id: "dev", value: Math.log((1 + 0.1 * zone.devices) / (1 + 0.1 * 4)) },
    { id: "sev", value: Math.log(multiplier) },
  ];
  const total = contributions.reduce((sum, item) => sum + Math.abs(item.value), 0) || 1;

  return contributions
    .filter((item) => Math.abs(item.value) >= 0.05)
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    .slice(0, 3)
    .map((item) => {
      const direction = item.value > 0 ? "raises" : "lowers";
      return {
        id: item.id,
        direction,
        weight: round2(Math.abs(item.value) / total),
        text: factorText(item.id, direction, rate, zone.devices),
      } satisfies RiskFactor;
    });
}

// Wording follows `factor_text` in backend/concierge/risk/explain.py, adapted from "your line" to "this area".
function factorText(id: string, direction: "raises" | "lowers", rate: number, devices: number): string {
  switch (id) {
    case "hist":
      if (direction === "raises") {
        return `The line serving this area had more storm damage than most lines in past storms (about ${rate} reports per 1,000 homes).`;
      }
      return `The line serving this area had less storm damage than most lines in past storms (about ${rate} reports per 1,000 homes).`;
    case "type":
      return direction === "lowers"
        ? "Most service here is underground, which is better protected from wind and trees."
        : "Most service here is overhead, which is more exposed to wind and trees.";
    case "dev": {
      const phrase = `${devices} protective ${plural(devices, "device", "devices")}`;
      return direction === "raises"
        ? `Power reaches this area through ${phrase}; more devices means more places a fault can cut service.`
        : `Power reaches this area through only ${phrase}, fewer than most areas.`;
    }
    default:
      return direction === "raises"
        ? "The storm scenario you picked is stronger than a typical past storm."
        : "The storm scenario you picked is milder than a typical past storm.";
  }
}

function buildRisk(zone: SampleZone, band: RiskBand, confidence: ConfidenceLevel, multiplier: number): RiskAssessment {
  const reasonRecords = `${zone.records} damage ${plural(zone.records, "report", "reports")} on the line serving this area in the 2 past storms we use.`;
  const evidence: Record<RiskBand, string> = {
    High: "In a backtest on past storms, lines rated High had damage reports more often than lines rated Medium or Low.",
    Medium: "In a backtest on past storms, lines rated Medium had damage reports more often than lines rated Low, and less often than lines rated High.",
    Low: "In a backtest on past storms, lines rated Low had damage reports less often than lines rated Medium or High.",
  };
  return {
    band,
    scopeNote:
      "This rating shows how storm-prone the power line serving this area has been. It is not a forecast for one home.",
    confidence: { level: confidence, records: zone.records, reason: reasonRecords },
    bandEvidence: evidence[band],
    topFactors: riskFactors(zone, multiplier),
  };
}

/* ------------------------------ restoration ------------------------------ */

function defaultDamage(zone: SampleZone, multiplier: number): Record<DamageKind, number> {
  const intensity = zone.base * Math.pow(multiplier, DAMPING);
  const damage = {} as Record<DamageKind, number>;
  (Object.keys(DAMAGE_PER_BASE) as DamageKind[]).forEach((kind) => {
    damage[kind] = Math.round(intensity * DAMAGE_PER_BASE[kind]);
  });
  // If an outage happens there is at least one repair.
  if (Object.values(damage).every((count) => count === 0)) damage.fuse = 1;
  return damage;
}

function rangeText(low: number, high: number): string {
  if (high < 48) return `about ${low} to ${high} hours`;
  const lowDays = Math.max(1, Math.round(low / 24));
  const highDays = Math.max(lowDays, Math.round(high / 24));
  return lowDays === highDays ? `about ${highDays} days` : `about ${lowDays} to ${highDays} days`;
}

function buildRestoration(
  zone: SampleZone,
  request: ZoneAssessmentRequest,
  multiplier: number,
  confidence: ConfidenceLevel,
  extrapolated: boolean,
): RestorationEstimate {
  const { scenario } = request;
  const assumedDamage = scenario.damage ?? defaultDamage(zone, multiplier);

  const directCrewHours = (Object.keys(CREW_HOURS) as DamageKind[]).reduce(
    (sum, kind) => sum + assumedDamage[kind] * CREW_HOURS[kind],
    0,
  );
  const repairHours = (directCrewHours * INDIRECT_LABOR_MULTIPLIER) / Math.max(1, scenario.crews);
  const accessFactor = scenario.access === "limited" ? 1.35 : 1;
  const upstreamWait = scenario.upstreamDamage ? 8 : 0;
  const middle = repairHours * accessFactor + upstreamWait + ASSESSMENT_HOURS;
  const spread = confidence === "Low" ? 1.6 : confidence === "Medium" ? 1.45 : 1.3;

  const lowHours = Math.max(1, Math.floor(middle * 0.7));
  const highHours = Math.max(lowHours + 1, Math.ceil(middle * spread));

  const movers: RestorationFactor[] = [
    {
      id: "damage",
      direction: "either",
      text: "Real damage may differ from what we assumed. Crews only know after they inspect.",
    },
  ];
  if (scenario.crews <= 1) {
    movers.push({ id: "crews", direction: "longer", text: "Only 1 crew is assigned, so repairs happen one after another." });
  } else if (scenario.crews >= 3) {
    movers.push({ id: "crews", direction: "shorter", text: `${scenario.crews} crews working at the same time shorten the range.` });
  }
  if (scenario.access === "limited") {
    movers.push({ id: "access", direction: "longer", text: "Blocked roads or hard-to-reach equipment add time to each repair." });
  }
  if (scenario.upstreamDamage) {
    movers.push({ id: "upstream", direction: "longer", text: "Damage upstream on the main line has to be fixed before repairs here can finish." });
  }
  if (extrapolated) {
    movers.push({ id: "extrapolated", direction: "longer", text: "This storm is stronger than any in our records, so damage could be worse than assumed." });
  }

  return {
    rangeText: rangeText(lowHours, highHours),
    lowHours,
    highHours,
    assumedDamage,
    confidence,
    movers,
    sequencingNote: scenario.upstreamDamage
      ? "Repairs on the main line come first. Homes can get power back in steps as each upstream section is fixed."
      : "Power often comes back in steps. Repairs closer to the power source restore the most homes first.",
  };
}

/* ----------------------------- preparation ------------------------------- */

function buildPreparation(band: RiskBand | null, highHours: number | null, needs: DeclaredNeed[]): Preparation {
  const safety: SafetyNotice[] = [
    {
      id: "downed-line",
      level: "emergency",
      title: "Downed power line",
      text: "Stay far back and treat every downed line as live. Call 911.",
    },
    {
      id: "generator",
      level: "caution",
      title: "Generator and carbon monoxide",
      text: "Never run a generator, grill, or camp stove inside your home or garage. Carbon monoxide can kill in minutes.",
    },
  ];
  if (needs.includes("medical-power")) {
    safety.unshift({
      id: "medical",
      level: "emergency",
      title: "If someone's health is at risk",
      text: "If anyone relies on powered medical equipment and it stops working, call 911. Don't wait for the power to come back.",
    });
  }

  const longOutage = band === "High" || (highHours !== null && highHours >= 24);
  const tailored = (id: string, text: string, tailoredFor: DeclaredNeed): PrepAction => ({ id, text, tailoredFor });

  const now: PrepAction[] = [
    { id: "charge", text: "Charge phones, tablets, and backup batteries." },
    { id: "light", text: "Find flashlights and spare batteries. Use them instead of candles." },
    { id: "numbers", text: "Save 911 and your utility's outage number in your phone." },
  ];
  const before: PrepAction[] = [];
  if (band !== "Low") {
    before.push(
      { id: "water", text: "Fill clean containers with drinking water." },
      { id: "fridge", text: "Set the fridge and freezer to their coldest setting." },
      { id: "surge", text: "Unplug sensitive electronics or use surge protectors." },
    );
  }
  if (longOutage) {
    before.push(
      { id: "food", text: "Stock food that needs no cooking, and fill prescriptions you will need." },
      { id: "fuel", text: "Fill your car's gas tank and keep some cash on hand." },
    );
  }
  const during: PrepAction[] = [
    { id: "doors", text: "Keep fridge and freezer doors closed." },
    { id: "neighbors", text: "When it is safe, check on neighbors, especially older adults." },
    { id: "report", text: "Report your outage to your utility instead of assuming they know." },
  ];
  const after: PrepAction[] = [
    { id: "food-safety", text: "Throw away perishable food that was above 40°F for more than 2 hours." },
    { id: "damage", text: "Report any damage you see, and stay clear of anything touching a line." },
  ];

  if (needs.includes("medical-power")) {
    now.push(
      tailored("med-plan", "Make a backup plan for powered medical equipment with your medical provider.", "medical-power"),
      tailored("med-battery", "Charge equipment batteries and know where the nearest alternate power is.", "medical-power"),
    );
    before.push(tailored("med-cold", "Ask your pharmacist how long medicine that must stay cold is safe without refrigeration.", "medical-power"));
  }
  if (needs.includes("accessibility")) {
    now.push(
      tailored("acc-plan", "Plan how you would get help or get out if elevators, garage doors, or powered mobility devices stop working.", "accessibility"),
      tailored("acc-contacts", "Keep a charged phone and a short list of people who can help.", "accessibility"),
    );
    before.push(tailored("acc-charge", "Fully charge any powered mobility device and keep the charger within reach.", "accessibility"));
  }
  if (needs.includes("critical-facility")) {
    now.push(
      tailored("fac-backup", "Test backup power and confirm you have enough fuel.", "critical-facility"),
      tailored("fac-plan", "Review your continuity plan with staff and your utility contact.", "critical-facility"),
    );
  }

  const groups: PrepGroup[] = [
    { id: "now", title: "Do now", actions: now },
    { id: "before", title: "Before the storm hits", actions: before },
    { id: "during", title: "If the power goes out", actions: during },
    { id: "after", title: "After", actions: after },
  ];
  return { safety, groups: groups.filter((group) => group.actions.length > 0) };
}

/* -------------------------------- alerts --------------------------------- */

function buildAlerts(
  band: RiskBand | null,
  highHours: number | null,
  facility: boolean,
  needs: DeclaredNeed[],
  unrated: boolean,
): AlertEligibility {
  const note = "Sample rules. The real thresholds are not final.";

  if (unrated) {
    return {
      eligible: true,
      summary: "We can't rate this area, so we suggest turning on alerts to stay informed.",
      rules: [{ id: "fallback", text: "When an area can't be rated reliably, alerts are offered so you aren't left without information.", met: true }],
      timing: "Updates when an outage is confirmed and when power is restored.",
      channels: needs.length > 0 ? ["Text message", "Email", "Phone call from a person"] : ["Text message", "Email"],
      escalation: ["If you are worried or need help, talk to a person."],
      note,
    };
  }

  const riskMet = band !== "Low";
  const longMet = highHours !== null && highHours >= 12;
  const needMet = needs.length > 0;
  const eligible = riskMet || longMet || needMet;

  const rules = [
    { id: "risk", text: "Outage risk is Medium or High in this storm scenario.", met: riskMet },
    { id: "long", text: "If an outage happens, the estimate reaches 12 hours or more.", met: longMet },
    { id: "need", text: "You told us about a need that makes power more important.", met: needMet },
    { id: "facility", text: "A hospital, fire station, or ambulance service is nearby, which moves alerts earlier.", met: facility },
  ];

  if (!eligible) {
    return {
      eligible,
      summary: "Proactive alerts aren't triggered at this risk level. You can still sign up and check outage status any time.",
      rules,
      timing: "No proactive alerts. You would still get updates if you sign up.",
      channels: [],
      escalation: [],
      note,
    };
  }

  const early = band === "High" || facility || needMet;
  const escalation = [
    "If the estimate grows by several hours, we send an \"estimate updated\" message.",
    "If power is still out after the end of the range, a person follows up.",
  ];
  if (needMet) escalation.push("Because you told us about a need, a person reviews your case once an outage is confirmed.");
  if (facility) escalation.push("A nearby critical facility moves this area up for priority review.");

  return {
    eligible,
    summary: "You qualify for proactive storm and outage alerts.",
    rules,
    timing: early
      ? "A storm alert about a day ahead of expected impact, then updates when an outage is confirmed, when the estimate changes, and when power is back."
      : "A storm alert when a warning is issued for your area, then updates when an outage is confirmed and when power is back.",
    channels: needMet ? ["Text message", "Email", "Phone call from a person"] : ["Text message", "Email"],
    escalation,
    note,
  };
}

/* ------------------------------- messages -------------------------------- */

function buildMessages(band: RiskBand, rangeLabel: string): ProactiveMessage[] {
  return [
    {
      stage: "pre-storm",
      channel: "Email",
      subject: "A storm is expected in your area",
      body:
        `A storm is expected to reach your area. In this scenario, outage risk for your area is ${band}.\n\n` +
        "Take a few minutes now: charge phones and backup batteries, gather flashlights and drinking water, and plan to keep fridge doors closed if the power goes out.\n\n" +
        "If you see a downed power line, stay far away and call 911.",
      origin: "template",
    },
    {
      stage: "outage-confirmed",
      channel: "Text message",
      body:
        `We've confirmed an outage in your area. Our early model estimate for power returning is ${rangeLabel}. ` +
        "This is an estimate and may change once crews see the damage. Stay away from downed lines. Emergency? Call 911.",
      origin: "template",
    },
    {
      stage: "estimate-updated",
      channel: "Text message",
      body:
        `Update: our estimate for restoring power in your area is now ${rangeLabel}. ` +
        "It is still an estimate and may change as crews learn more.",
      origin: "template",
    },
    {
      stage: "restored",
      channel: "Text message",
      body:
        "Power has been restored in your area. If yours is still out, please report it. " +
        "Check your appliances, and thank you for your patience.",
      origin: "template",
    },
  ];
}

/* ----------------------------- transparency ------------------------------ */

function buildTransparency(zone: SampleZone, severity: SeverityInfo, rated: boolean) {
  const assumptions = [
    "This preview uses placeholder numbers. It is not the real model.",
    "Storm strength is a scenario you choose, not a weather forecast.",
  ];
  const missingData = [
    "No weather data is used.",
    "Damage reports show where repairs were needed, not which homes lost power.",
    "No live crew locations, road conditions, or confirmed outage information is connected.",
  ];
  const couldChange = [
    "A storm that hits your area harder or softer than past storms.",
    "Tree trimming or line upgrades done since the last storm.",
    "New damage reports on your line as a storm unfolds.",
  ];

  if (rated) {
    assumptions.push(
      "Risk comes from damage reports on the line serving this area in the 2 past storms we use.",
      "How much service type and place on the line matter is an assumption, not something we measured.",
      `Repair time multiplies representative labor hours by ${INDIRECT_LABOR_MULTIPLIER} to cover travel, setup, and safety time.`,
      "Crews are assumed to work at the same time on separate repairs.",
    );
    if (severity.extrapolated) {
      assumptions.push("This scenario is stronger than any storm in our history, so the rating is an extrapolation.");
    }
    if (zone.service === "mixed") {
      missingData.push("This area mixes overhead and underground service, so service type is not used as a factor.");
    }
  } else {
    missingData.push("Not enough storm history or too few homes on this line to rate it reliably.");
    couldChange.push("More storm history for this line would let us rate it.");
  }
  return { assumptions, missingData, couldChange };
}

/* ------------------------------- assemble -------------------------------- */

export function sampleAssessment(request: ZoneAssessmentRequest): ZoneAssessment | null {
  const zone = SAMPLE_ZONES.find((candidate) => candidate.id === request.zoneId);
  if (!zone) return null;

  const severity = severityInfo(request.scenario.severity);
  const needs = request.declaredNeeds;
  const confirmed = {
    available: false,
    note:
      "No confirmed outage information is connected to this preview. When the utility confirms an outage or a restoration time for your area, it will appear here and be labeled as confirmed.",
  };

  if (zone.fallback) {
    return {
      source: "sample",
      zoneId: zone.id,
      zoneName: zone.name,
      status: "insufficient_data",
      severity,
      criticalFacilityNearby: zone.facility,
      confirmed,
      risk: null,
      restoration: null,
      preparation: buildPreparation(null, null, needs),
      alerts: buildAlerts(null, null, zone.facility, needs, true),
      messages: [],
      transparency: buildTransparency(zone, severity, false),
      fallback: { reasonCode: zone.fallback, message: FALLBACK_MESSAGES[zone.fallback] },
    };
  }

  const band = bandFor(zone, severity.multiplier);
  const confidence = confidenceFor(zone, severity.extrapolated);
  const restoration = buildRestoration(zone, request, severity.multiplier, confidence, severity.extrapolated);

  return {
    source: "sample",
    zoneId: zone.id,
    zoneName: zone.name,
    status: "ok",
    severity,
    criticalFacilityNearby: zone.facility,
    confirmed,
    risk: buildRisk(zone, band, confidence, severity.multiplier),
    restoration,
    preparation: buildPreparation(band, restoration.highHours, needs),
    alerts: buildAlerts(band, restoration.highHours, zone.facility, needs, false),
    messages: buildMessages(band, restoration.rangeText),
    transparency: buildTransparency(zone, severity, true),
    fallback: null,
  };
}
