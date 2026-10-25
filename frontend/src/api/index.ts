import { httpClient } from "./httpClient";
import { mockClient } from "./mockClient";
import type { ConciergeClient } from "./types";

/**
 * Single swap point. The outage map and the chat answers talk to the real API. Only the storm summary on the chat page
 * still comes from the placeholder client.
 */
export const conciergeClient: ConciergeClient = { ...mockClient, ...httpClient };

export type * from "./types";
