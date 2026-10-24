import { useEffect, useState } from "react";
import type { ConciergeClient, EventSummary } from "../api";

export type SummaryState =
  | { status: "loading" }
  | { status: "ready"; summary: EventSummary }
  | { status: "error" };

/** Loads the storm summary and reloads it whenever the scoped location changes. */
export function useEventSummary(client: ConciergeClient, location: string | null): SummaryState {
  const [state, setState] = useState<SummaryState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });

    client
      .getEventSummary(location, controller.signal)
      .then((summary) => setState({ status: "ready", summary }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setState({ status: "error" });
      });

    return () => controller.abort();
  }, [client, location]);

  return state;
}
