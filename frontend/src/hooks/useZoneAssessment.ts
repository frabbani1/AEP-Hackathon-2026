import { useCallback, useEffect, useRef, useState } from "react";
import type { ConciergeClient, DeclaredNeed, Scenario, ZoneAssessment } from "../api";

export type AssessmentState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: ZoneAssessment; refreshing: boolean }
  | { status: "error" };

/** Wait for what-if changes to settle (for example rapid taps on a stepper) before asking. */
const SETTLE_MS = 200;

/**
 * Loads the full assessment for the selected area and reloads it when the scenario or the
 * declared needs change. `scenario` and `declaredNeeds` must be referentially stable between
 * renders when unchanged. Switching area clears the old answer; changing the scenario keeps
 * it on screen, marked as refreshing, until the new one arrives.
 */
export function useZoneAssessment(
  client: ConciergeClient,
  zoneId: string | null,
  scenario: Scenario,
  declaredNeeds: DeclaredNeed[],
) {
  const [state, setState] = useState<AssessmentState>({ status: "idle" });
  const [attempt, setAttempt] = useState(0);
  const lastZoneId = useRef<string | null>(null);

  useEffect(() => {
    if (!zoneId) {
      lastZoneId.current = null;
      setState({ status: "idle" });
      return;
    }

    const controller = new AbortController();
    const zoneChanged = lastZoneId.current !== zoneId;
    lastZoneId.current = zoneId;

    setState((previous) =>
      zoneChanged || previous.status !== "ready" ? { status: "loading" } : { ...previous, refreshing: true },
    );

    const timer = window.setTimeout(
      () => {
        client
          .getZoneAssessment({ zoneId, scenario, declaredNeeds }, controller.signal)
          .then((data) => setState({ status: "ready", data, refreshing: false }))
          .catch((error: unknown) => {
            if (error instanceof DOMException && error.name === "AbortError") return;
            setState({ status: "error" });
          });
      },
      zoneChanged ? 0 : SETTLE_MS,
    );

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [client, zoneId, scenario, declaredNeeds, attempt]);

  const retry = useCallback(() => {
    lastZoneId.current = null;
    setAttempt((count) => count + 1);
  }, []);

  return { state, retry };
}
