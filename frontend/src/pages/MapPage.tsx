import { useCallback, useState } from "react";
import { conciergeClient } from "../api";
import type { DeclaredNeed, Scenario, SeverityPreset } from "../api";
import { AreaPicker } from "../components/map/AreaPicker";
import { MapLegend } from "../components/map/MapLegend";
import { ScenarioControls } from "../components/map/ScenarioControls";
import { ZoneMap } from "../components/map/ZoneMap";
import { ZonePanel } from "../components/map/ZonePanel";
import { useMapZones } from "../hooks/useMapZones";
import { useZoneAssessment } from "../hooks/useZoneAssessment";
import { DEFAULT_SCENARIO } from "../lib/scenario";
import "./MapPage.css";

/**
 * Outage map. A customer picks an anonymized area, chooses a storm scenario, and gets the
 * four answers from the brief: risk, restoration range, preparation, and alert eligibility.
 * They can change the scenario and watch every answer respond.
 */
export function MapPage() {
  const [scenario, setScenario] = useState<Scenario>(DEFAULT_SCENARIO);
  const [needs, setNeeds] = useState<DeclaredNeed[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const zones = useMapZones(conciergeClient, scenario.severity);
  const assessment = useZoneAssessment(conciergeClient, selectedId, scenario, needs);

  const patchScenario = useCallback(
    (patch: Partial<Scenario>) => setScenario((current) => ({ ...current, ...patch })),
    [],
  );

  // The damage assumption belongs to one area in one storm, so it starts over when either changes.
  const clearDamage = () => setScenario((current) => (current.damage === null ? current : { ...current, damage: null }));

  const selectArea = (id: string) => {
    if (id === selectedId) return;
    setSelectedId(id);
    clearDamage();
  };

  const changeSeverity = (severity: SeverityPreset) => patchScenario({ severity, damage: null });

  const resetWhatIf = () =>
    setScenario((current) => ({ ...DEFAULT_SCENARIO, severity: current.severity }));

  const zonesReady = zones.state.status === "ready" ? zones.state : null;

  return (
    <main id="main" className="map-page" tabIndex={-1}>
      <div className="container map-page__intro">
        <h1>Outage map</h1>
        <p className="map-page__lede">
          Pick an area, choose a storm, and see what it could mean: your outage risk, how long repairs could take,
          how to prepare, and whether you qualify for alerts. Change the storm or the repair assumptions and watch
          every answer update.
        </p>
        {zonesReady?.data.source === "sample" && (
          <p className="sample-banner" role="note">
            <strong>Sample data.</strong> The areas, risk levels, and estimates on this page are placeholders that
            show how it will work. They are not real predictions or confirmed utility information.
          </p>
        )}
      </div>

      <div className="container map-page__layout">
        <div className="map-page__map">
          {zones.state.status === "loading" && (
            <div className="map-page__placeholder" role="status">
              Loading the map&hellip;
            </div>
          )}
          {zones.state.status === "error" && (
            <div className="map-page__placeholder map-page__placeholder--error" role="alert">
              <p>We couldn&rsquo;t load the map.</p>
              <button type="button" className="btn btn-outline-danger" onClick={zones.retry}>
                Try again
              </button>
            </div>
          )}
          {zonesReady?.failed && (
            <p className="map-page__notice" role="alert">
              We couldn&rsquo;t update the map for this storm, so it still shows the previous one.{" "}
              <button type="button" className="link-button" onClick={zones.retry}>
                Try again
              </button>
            </p>
          )}
          {zonesReady && (
            <>
              <p id="map-help" className="sr-only">
                This map is a visual aid. To choose an area with a keyboard or screen reader, use the Choose an
                area list. When the map has focus, arrow keys move it and plus and minus zoom it.
              </p>
              <ZoneMap
                zones={zonesReady.data.zones}
                bounds={zonesReady.data.bounds}
                selectedId={selectedId}
                onSelect={selectArea}
                describedBy="map-help"
              />
              <MapLegend minHomesPerArea={zonesReady.data.minHomesPerArea} />
            </>
          )}
        </div>

        <div className="map-page__panel">
          <section className="setup" aria-labelledby="setup-title">
            <h2 id="setup-title" className="setup__title">
              1. Choose an area and a storm
            </h2>
            {zonesReady ? (
              <AreaPicker zones={zonesReady.data.zones} selectedId={selectedId} onSelect={selectArea} />
            ) : (
              <p className="field__hint">The list of areas appears when the map has loaded.</p>
            )}
            <ScenarioControls
              severity={scenario.severity}
              updating={zonesReady?.refreshing ?? false}
              onChange={changeSeverity}
            />
          </section>

          <section aria-label="Answers for the chosen area" className="results">
            <ZonePanel
              state={assessment.state}
              scenario={scenario}
              minHomesPerArea={zonesReady?.data.minHomesPerArea ?? null}
              needs={needs}
              onNeedsChange={setNeeds}
              onScenarioChange={patchScenario}
              onResetWhatIf={resetWhatIf}
              onRetry={assessment.retry}
            />
          </section>
        </div>
      </div>
    </main>
  );
}
