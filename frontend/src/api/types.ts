/**
 * Contract between the chat and map UI and the Concierge API.
 *
 * Keep the UI talking to `ConciergeClient` only. `httpClient.ts` implements the map methods against the real API
 * (`POST /zones`, `POST /zones/assess`, `GET /evidence`); `mockClient.ts` still stands in for the chat, which has no
 * backend yet. `api/index.ts` is the single place that picks between them.
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
/* comes from these responses, which `backend/concierge/zones/view.py` builds   */
/* from the risk, restoration, assistant and alerts engines. The wire format is */
/* snake_case; `httpClient.ts` converts it to the camelCase used here.          */
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
  source: "live";
  severity: SeverityInfo;
  /** Where to frame the map first. */
  bounds: LatLngBounds;
  /** Smallest group of homes an area may represent. Shown in the privacy note. */
  minHomesPerArea: number;
  zones: MapZone[];
}

export type DamageKind = "pole" | "fuse" | "primary" | "transformer" | "secondary";
export type AccessCondition = "normal" | "restricted" | "remote";

/** What-if inputs that change the restoration estimate. */
export interface Scenario {
  severity: SeverityPreset;
  /** Crews assigned to this area. */
  crews: number;
  access: AccessCondition;
  /**
   * Total repair jobs to assume, drawn from the mix of past damage. Null means "let the model decide from this area
   * and storm". Ignored when `damage` is set.
   */
  damageCount: number | null;
  /** Repairs by kind, chosen by the customer. Null means "not chosen". Takes precedence over `damageCount`. */
  damage: Record<DamageKind, number> | null;
}

/** Needs the customer chose to declare. Never inferred. Used only to tailor advice and alerts. */
export type DeclaredNeed = "medical-power" | "accessibility" | "heat-cooling";

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
  /** A plain-language paragraph. `origin` says whether a model phrased it or code did. */
  explanation: { text: string; origin: "template" | "model" } | null;
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

export interface RestorationStep {
  stage: "feeder" | "lateral" | "service";
  label: string;
  /** Hours by which this stage is done in the likely (P50) outcome. */
  doneByHours: number;
}

export interface RestorationEstimate {
  /** Ready-to-display range such as "about 12 to 28 hours". */
  rangeText: string;
  /** Half of the simulated outcomes finish within this many hours. */
  p50Hours: number;
  /** Nine in ten simulated outcomes finish within this many hours. */
  p90Hours: number;
  /** One plain sentence saying what the range means. */
  meaning: string;
  /** "estimate, not confirmed" */
  label: string;
  /** Repair jobs the estimate assumes, so the what-if control starts from it. */
  damageCount: number | null;
  damageSource: "simulated" | "you_provided";
  confidence: ConfidenceLevel;
  /** Factors most likely to move the range. */
  movers: RestorationFactor[];
  /** How power comes back in stages, nearest the source first. */
  steps: RestorationStep[];
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
  id: "before" | "during" | "after";
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

export type AlertLevel = "info" | "standard" | "elevated" | "priority";

export interface AlertEligibility {
  eligible: boolean;
  /** Prototype alert level. Not a service commitment. */
  level: AlertLevel;
  levelReason: string;
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
  source: "live";
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

/** Aggregate backtest results from `GET /evidence`. Two past storms, circuit level, no identifiers. */
export interface EvidenceVerdict {
  code: string;
  level: "supports" | "mixed" | "does_not_support" | "not_testable";
  sentence: string;
}

export interface Evidence {
  /** "unavailable" until a reviewed backtest report is published. */
  status: "ok" | "unavailable";
  note: string;
  label: string | null;
  verdicts: EvidenceVerdict[];
  limits: string[];
}

export interface ConciergeClient {
  getEventSummary(location: string | null, signal?: AbortSignal): Promise<EventSummary>;
  ask(request: AskRequest, signal?: AbortSignal): Promise<AssistantReply>;
  getMapZones(request: MapZonesRequest, signal?: AbortSignal): Promise<MapZonesResponse>;
  getZoneAssessment(request: ZoneAssessmentRequest, signal?: AbortSignal): Promise<ZoneAssessment>;
  getEvidence(signal?: AbortSignal): Promise<Evidence>;
}
