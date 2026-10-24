/**
 * Contract between the chat and map UI and the (not yet built) Concierge API.
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

/* -------------------------------------------------------------------------- */
/* Outage map                                                                  */
/*                                                                             */
/* The map page never computes a number. Every figure and sentence it shows     */
/* comes from these responses. Risk fields mirror `RESULT_SCHEMA` in           */
/* `backend/concierge/risk/schema.py`. Restoration, preparation, alerts and    */
/* messages are proposed shapes for engines that do not exist yet.             */
/*                                                                             */
/* Privacy: a map "area" is a zone of at least `minHomesPerArea` homes. No      */
/* address, premise ID, device ID, circuit label, or point coordinate may      */
/* appear here. Polygons are generalized areas, never service locations.       */
/* -------------------------------------------------------------------------- */

export type RiskBand = "Low" | "Medium" | "High";
export type ConfidenceLevel = "Low" | "Medium" | "High";

/** Same ids as the risk engine's severity presets, in increasing order of strength. */
export type SeverityPreset = "sep-2024-like" | "typical" | "feb-2025-like" | "beyond-observed";

export interface SeverityInfo {
  preset: SeverityPreset | "custom";
  multiplier: number;
  /** True when the scenario is stronger than any storm in the history the model uses. */
  extrapolated: boolean;
}

/** Longitude then latitude, the GeoJSON order. */
export type LngLat = [longitude: number, latitude: number];
/** [[south, west], [north, east]] */
export type LatLngBounds = [[number, number], [number, number]];

export interface MapZone {
  /** Opaque, browser-safe zone token. */
  id: string;
  /** Friendly anonymous label such as "Area 7". */
  name: string;
  /** Outer ring of the generalized area. Not a service location. */
  ring: LngLat[];
  /** Null when there is not enough history to rate the area (see `ZoneAssessment.fallback`). */
  band: RiskBand | null;
  confidence: ConfidenceLevel | null;
}

export interface MapZonesRequest {
  severity: SeverityPreset;
}

export interface MapZonesResponse {
  /** "sample" means placeholder content, not a real prediction. */
  source: "sample" | "live";
  severity: SeverityInfo;
  /** Where to frame the map first. */
  bounds: LatLngBounds;
  /** Smallest group of homes an area may represent. Shown in the privacy note. */
  minHomesPerArea: number;
  zones: MapZone[];
}

export type DamageKind = "pole" | "fuse" | "primary" | "transformer" | "secondary";
export type AccessCondition = "normal" | "limited";

/** What-if inputs that change the restoration estimate. */
export interface Scenario {
  severity: SeverityPreset;
  /** Crews assigned to this area. */
  crews: number;
  access: AccessCondition;
  /** Assume main-line damage upstream, so repairs here wait for it. */
  upstreamDamage: boolean;
  /** Null means "use the model's own damage assumption for this area and storm". */
  damage: Record<DamageKind, number> | null;
}

/** Needs the customer chose to declare. Never inferred. Used only to tailor advice and alerts. */
export type DeclaredNeed = "medical-power" | "accessibility" | "critical-facility";

export interface ZoneAssessmentRequest {
  zoneId: string;
  scenario: Scenario;
  declaredNeeds: DeclaredNeed[];
}

export interface RiskFactor {
  id: string;
  direction: "raises" | "lowers";
  /** Share of the total contribution, 0 to 1. */
  weight: number;
  text: string;
}

export interface RiskAssessment {
  band: RiskBand;
  /** Plain-language statement of what the rating does and does not describe. */
  scopeNote: string;
  confidence: { level: ConfidenceLevel; records: number; reason: string };
  /** How well bands like this one held up in a backtest, when available. */
  bandEvidence: string | null;
  topFactors: RiskFactor[];
}

export interface RestorationFactor {
  id: string;
  /** Which way this factor is pushing the range now. "either" means it is an unknown. */
  direction: "longer" | "shorter" | "either";
  text: string;
}

export interface RestorationEstimate {
  /** Ready-to-display range such as "about 12 to 28 hours". */
  rangeText: string;
  lowHours: number;
  highHours: number;
  /** Damage the estimate assumes, so the what-if controls start from it. */
  assumedDamage: Record<DamageKind, number>;
  confidence: ConfidenceLevel;
  /** Factors most likely to move the range. */
  movers: RestorationFactor[];
  /** Explains step restoration when upstream repairs matter. */
  sequencingNote: string | null;
}

export interface SafetyNotice {
  id: string;
  level: "emergency" | "caution";
  title: string;
  text: string;
}

export interface PrepAction {
  id: string;
  text: string;
  /** Set when the action was added because of a need the customer declared. */
  tailoredFor?: DeclaredNeed;
}

export interface PrepGroup {
  id: "now" | "before" | "during" | "after";
  title: string;
  actions: PrepAction[];
}

export interface Preparation {
  safety: SafetyNotice[];
  groups: PrepGroup[];
}

export interface AlertRule {
  id: string;
  text: string;
  met: boolean;
}

export interface AlertEligibility {
  eligible: boolean;
  summary: string;
  rules: AlertRule[];
  timing: string;
  channels: string[];
  escalation: string[];
  /** Caveat about how final the rules are. */
  note: string | null;
}

export type MessageStage = "pre-storm" | "outage-confirmed" | "estimate-updated" | "restored";

export interface ProactiveMessage {
  stage: MessageStage;
  channel: "Text message" | "Email";
  subject?: string;
  body: string;
  /** "template" means fixed wording filled with engine numbers. */
  origin: "template" | "model";
}

export interface Transparency {
  assumptions: string[];
  missingData: string[];
  couldChange: string[];
}

export interface ConfirmedInfo {
  available: boolean;
  note: string;
}

export interface ZoneAssessment {
  source: "sample" | "live";
  zoneId: string;
  zoneName: string;
  status: "ok" | "insufficient_data";
  severity: SeverityInfo;
  /** A critical facility (hospital, fire or ambulance) is near. Never says which or where. */
  criticalFacilityNearby: boolean;
  confirmed: ConfirmedInfo;
  /** Null when `status` is "insufficient_data". */
  risk: RiskAssessment | null;
  /** Null when `status` is "insufficient_data". */
  restoration: RestorationEstimate | null;
  preparation: Preparation;
  alerts: AlertEligibility;
  /** Empty when `status` is "insufficient_data". */
  messages: ProactiveMessage[];
  transparency: Transparency;
  /** Set when the model cannot make a reliable prediction. */
  fallback: { reasonCode: string; message: string } | null;
}

export interface ConciergeClient {
  getEventSummary(location: string | null, signal?: AbortSignal): Promise<EventSummary>;
  ask(request: AskRequest, signal?: AbortSignal): Promise<AssistantReply>;
  getMapZones(request: MapZonesRequest, signal?: AbortSignal): Promise<MapZonesResponse>;
  getZoneAssessment(request: ZoneAssessmentRequest, signal?: AbortSignal): Promise<ZoneAssessment>;
}
