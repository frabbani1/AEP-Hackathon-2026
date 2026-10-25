import { useCallback, useEffect, useState } from "react";
import type { ConciergeClient, Evidence } from "../api";

export type EvidenceState = { status: "loading" } | { status: "ready"; data: Evidence } | { status: "error" };

/** Loads the backtest summary once. It describes two past storms and does not depend on the chosen area. */
export function useEvidence(client: ConciergeClient): EvidenceState {
  const [state, setState] = useState<EvidenceState>({ status: "loading" });

  const load = useCallback(
    (signal: AbortSignal) => {
      client
        .getEvidence(signal)
        .then((data) => setState({ status: "ready", data }))
        .catch((error: unknown) => {
          if (error instanceof DOMException && error.name === "AbortError") return;
          setState({ status: "error" });
        });
    },
    [client],
  );

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return state;
}
