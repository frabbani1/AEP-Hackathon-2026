import { httpClient } from "./httpClient";
import { mockClient } from "./mockClient";
import type { ConciergeClient } from "./types";

/**
 * Single swap point. The outage map talks to the real API. The chat has no backend yet (a typed city or ZIP does not
 * map to an area), so it keeps the placeholder client until the assistant can be scoped to a map area.
 */
export const conciergeClient: ConciergeClient = { ...mockClient, ...httpClient };

export type * from "./types";
