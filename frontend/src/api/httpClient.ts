import type {
  AccessCondition,
  AlertEmailResult,
  ConciergeClient,
  DamageKind,
  DeclaredNeed,
  Evidence,
  FindAreaResult,
  MapZonesRequest,
  MapZonesResponse,
  OutlookResult,
  Scenario,
  ZoneAssessment,
  ZoneAssessmentRequest,
} from "./types";

/**
 * HTTP implementation of `ConciergeClient` (the map, the area lookup and the evidence), for the Concierge API (see docs/api.md).
 *
 * Base URL: `VITE_API_URL`, else same-origin `/api` (CloudFront routes `/api/*` to the API; the Vite dev server
 * proxies it to `scripts/dev_api.py`). The API has no authentication. It only ever sees opaque area ids, never a
 * premise token, address or coordinate typed by the customer.
 *
 * Responses are snake_case; `camelize` renames the keys. Values are never touched.
 */

const API_BASE: string = (import.meta.env.VITE_API_URL ?? "/api").replace(/\/+$/, "");
/** Ask the model to phrase the text. Off by default: each answer makes several sequential model calls. */
const USE_LLM = import.meta.env.VITE_ASSISTANT_LLM === "true";
const TIMEOUT_MS = 30_000;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const camelKey = (key: string) => key.replace(/_([a-z0-9])/g, (_, char: string) => char.toUpperCase());

/** Rename object keys from snake_case to camelCase, recursively. */
export function camelize<T>(value: unknown): T {
  if (Array.isArray(value)) return value.map((item) => camelize(item)) as T;
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) out[camelKey(key)] = camelize(item);
    return out as T;
  }
  return value as T;
}

/** The API's asset types for the five kinds of damage the what-if controls offer. */
const ASSET_TYPE: Record<DamageKind, string> = {
  pole: "pole",
  fuse: "fuse_bank",
  primary: "primary_line",
  transformer: "transformer",
  secondary: "secondary_line",
};

const NEED_FIELD: Record<DeclaredNeed, string> = {
  "medical-power": "medical_equipment",
  accessibility: "mobility_limited",
  "heat-cooling": "electric_heat_or_cooling",
};

function needsBody(needs: DeclaredNeed[]): Record<string, boolean> {
  const body: Record<string, boolean> = {};
  for (const [need, field] of Object.entries(NEED_FIELD)) body[field] = needs.includes(need as DeclaredNeed);
  return body;
}

/** Customer-chosen damage wins over a total count; all zeros means "not chosen". */
function damageBody(scenario: Scenario): Record<string, unknown> {
  if (scenario.damage) {
    const items = (Object.keys(ASSET_TYPE) as DamageKind[])
      .filter((kind) => scenario.damage![kind] > 0)
      .map((kind) => ({ asset_type: ASSET_TYPE[kind], qty: scenario.damage![kind] }));
    if (items.length > 0) return { damage: items };
  }
  return scenario.damageCount === null ? {} : { damage_count: scenario.damageCount };
}

export function assessmentBody(request: ZoneAssessmentRequest): Record<string, unknown> {
  const { scenario } = request;
  return {
    zone_id: request.zoneId,
    severity: scenario.severity,
    crews: scenario.crews,
    access: scenario.access satisfies AccessCondition,
    needs: needsBody(request.declaredNeeds),
    use_llm: USE_LLM,
    ...damageBody(scenario),
  };
}

async function call<T>(path: string, init: { method: "GET" | "POST"; body?: unknown }, signal?: AbortSignal): Promise<T> {
  const timeout = AbortSignal.timeout(TIMEOUT_MS);
  const response = await fetch(`${API_BASE}${path}`, {
    method: init.method,
    headers: init.body === undefined ? undefined : { "content-type": "application/json" },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const payload = (await response.json()) as { error?: { message?: string } };
      if (payload.error?.message) message = payload.error.message;
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(response.status, message);
  }
  return camelize<T>(await response.json());
}

type ApiMethods = Pick<
  ConciergeClient,
  "getMapZones" | "getZoneAssessment" | "getEvidence" | "findArea" | "getOutlook" | "sendAlertEmail"
>;

export const httpClient: ApiMethods = {
  sendAlertEmail(email, target, signal) {
    // Consent is always sent as true because the form will not submit without the box ticked.
    const content = target.kind === "area" ? assessmentBody(target.request) : { query: target.query };
    return call<AlertEmailResult>("/alerts/email", { method: "POST", body: { ...content, email, consent: true } }, signal);
  },

  getOutlook(query, signal) {
    return call<OutlookResult>("/outlook", { method: "POST", body: { query } }, signal);
  },

  findArea(query, signal) {
    return call<FindAreaResult>("/zones/find", { method: "POST", body: { query } }, signal);
  },

  getMapZones({ severity }: MapZonesRequest, signal) {
    return call<MapZonesResponse>("/zones", { method: "POST", body: { severity } }, signal);
  },

  getZoneAssessment(request, signal) {
    return call<ZoneAssessment>("/zones/assess", { method: "POST", body: assessmentBody(request) }, signal);
  },

  async getEvidence(signal) {
    const evidence = await call<Partial<Evidence>>("/evidence", { method: "GET" }, signal);
    return {
      status: evidence.status ?? "unavailable",
      note: evidence.note ?? "",
      label: evidence.label ?? null,
      verdicts: evidence.verdicts ?? [],
      limits: evidence.limits ?? [],
    };
  },
};
