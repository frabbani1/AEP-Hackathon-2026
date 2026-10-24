import { mockClient } from "./mockClient";
import type { ConciergeClient } from "./types";

/** Single swap point: replace with an HTTP client once the API is available. */
export const conciergeClient: ConciergeClient = mockClient;

export type * from "./types";
