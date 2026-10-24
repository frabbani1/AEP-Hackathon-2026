import { useCallback, useEffect, useState } from "react";
import type { ConciergeClient, MapZonesResponse, SeverityPreset } from "../api";

export type ZonesState =
  | { status: "loading" }
  /** `failed` means the latest reload did not work and `data` is from an earlier scenario. */
  | { status: "ready"; data: MapZonesResponse; refreshing: boolean; failed: boolean }
  | { status: "error" };

/**
 * Loads the colored areas for the map. Reloads when the storm scenario changes and keeps
 * showing the previous areas while the new ones load (or if loading fails), so the map
 * never blanks out under someone who is using it.
 */
export function useMapZones(client: ConciergeClient, severity: SeverityPreset) {
  const [state, setState] = useState<ZonesState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState((previous) =>
      previous.status === "ready" ? { ...previous, refreshing: true, failed: false } : { status: "loading" },
    );

    client
      .getMapZones({ severity }, controller.signal)
      .then((data) => setState({ status: "ready", data, refreshing: false, failed: false }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setState((previous) =>
          previous.status === "ready" ? { ...previous, refreshing: false, failed: true } : { status: "error" },
        );
      });

    return () => controller.abort();
  }, [client, severity, attempt]);

  const retry = useCallback(() => setAttempt((count) => count + 1), []);
  return { state, retry };
}
