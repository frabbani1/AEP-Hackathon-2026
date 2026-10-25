import { useCallback, useState } from "react";
import { conciergeClient } from "../api";
import type { DeclaredNeed, Scenario, SeverityPreset, WeatherOutlook } from "../api";
import { AreaFinder } from "../components/map/AreaFinder";
import { AreaPicker } from "../components/map/AreaPicker";
import { EvidenceCard } from "../components/map/EvidenceCard";
import { MapLegend } from "../components/map/MapLegend";
import { OutlookCard } from "../components/map/OutlookCard";
import { ScenarioControls } from "../components/map/ScenarioControls";
import { ZoneMap } from "../components/map/ZoneMap";
import { ZonePanel } from "../components/map/ZonePanel";
import { useEvidence } from "../hooks/useEvidence";
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
  /** A forecast-only outlook for a place with no outage data. It replaces the area answers until the customer leaves it. */
  const [outlook, setOutlook] = useState<WeatherOutlook | null>(null);
  const [focus, setFocus] = useState<{ id: string; nonce: number } | null>(null);

  const zones = useMapZones(conciergeClient, scenario.severity);
  const assessment = useZoneAssessment(conciergeClient, selectedId, scenario, needs);
  const evidence = useEvidence(conciergeClient);

  const patchScenario = useCallback(
    (patch: Partial<Scenario>) => setScenario((current) => ({ ...current, ...patch })),
    [],
  );

  // The damage assumption belongs to one area in one storm, so it starts over when either changes.
  const clearDamage = () =>
    setScenario((current) =>
      current.damage === null && current.damageCount === null ? current : { ...current, damage: null, damageCount: null },
    );

  const selectArea = (id: string) => {
    setOutlook(null);
    if (id === selectedId) return;
    setSelectedId(id);
    clearDamage();
  };

  const showOutlook = (next: WeatherOutlook) => {
    setSelectedId(null);
    clearDamage();
    setOutlook(next);
  };

  // An area found by searching: select it and move the map to it.
  const findArea = (id: string) => {
    selectArea(id);
    setFocus((current) => ({ id, nonce: (current?.nonce ?? 0) + 1 }));
  };

  const changeSeverity = (severity: SeverityPreset) => patchScenario({ severity, damage: null, damageCount: null });

  const resetWhatIf = () =>
    setScenario((current) => ({ ...DEFAULT_SCENARIO, severity: current.severity }));

  const zonesReady = zones.state.status === "ready" ? zones.state : null;

  return (
    <main id="main" className="map-page" tabIndex={-1}>
      <div className="container map-page__intro">
        <h1>Outage map</h1>
        <p className="map-page__lede">
          Pick an area and see what the forecast could mean: your outage risk, how long repairs could take, how to
          prepare, and whether you qualify for alerts. Want to explore &ldquo;what if&rdquo;? Switch to a past storm
          and watch every answer update.
        </p>
        <p className="estimate-banner" role="note">
          <strong>Model estimates, not confirmed information.</strong> Each area is rated through one typical home in
          it, using damage reports from two past storms. Weather is a forecast from Open-Meteo, not an official
          warning. Nothing on this page is a confirmed utility restoration time.
        </p>
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
                outlook={outlook}
                focus={focus}
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
              <>
                <AreaFinder client={conciergeClient} onFound={findArea} onOutlook={showOutlook} />
                <AreaPicker zones={zonesReady.data.zones} selectedId={selectedId} onSelect={selectArea} />
              </>
            ) : (
              <p className="field__hint">The list of areas appears when the map has loaded.</p>
            )}
            <ScenarioControls
              severity={scenario.severity}
              updating={zonesReady?.refreshing ?? false}
              weather={zonesReady?.data.weather ?? null}
              onChange={changeSeverity}
            />
          </section>

          <section aria-label={outlook ? "Forecast-only outlook" : "Answers for the chosen area"} className="results">
            {outlook ? (
              <OutlookCard outlook={outlook} onClose={() => setOutlook(null)} />
            ) : (
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
            )}
          </section>

          <EvidenceCard state={evidence} />
        </div>
      </div>
    </main>
  );
}
