import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef, useState } from "react";
import type { LatLngBounds, LngLat, MapZone, RiskBand, WeatherOutlook } from "../../api";
import { BAND_LABEL, NOT_RATED_LABEL } from "../../lib/bands";
import "./ZoneMap.css";

/**
 * Background tiles. Set VITE_BASEMAP_URL to another tile template, or to an empty string to
 * draw the areas on a plain background with no third-party requests. Tile requests tell the
 * tile host which part of the map is in view, so the zoom is capped (MAX_ZOOM) well short
 * of street level.
 */
const DEFAULT_BASEMAP_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
// Credit is plain text on purpose: the map must not send people to outside sites. The About page repeats it.
const DEFAULT_BASEMAP_ATTRIBUTION = "&copy; OpenStreetMap contributors";
const BASEMAP_URL: string = import.meta.env.VITE_BASEMAP_URL ?? DEFAULT_BASEMAP_URL;
const BASEMAP_ATTRIBUTION: string = import.meta.env.VITE_BASEMAP_ATTRIBUTION ?? DEFAULT_BASEMAP_ATTRIBUTION;

/** Same purple as `--forecast` in base.css. Leaflet needs a literal color, and the CSS variable is read below at runtime. */
const FORECAST_COLOR_FALLBACK = "#6b2fa0";
const MAX_ZOOM = 12;
/** Zoom out far enough to see the whole region around the areas (and beyond), so the map is not boxed in. */
const MIN_ZOOM = 5;
/** Below this zoom the labels show only the area name; the risk word is left to the color and the tooltip. */
const BAND_LABEL_ZOOM = 10;

interface Props {
  zones: MapZone[];
  bounds: LatLngBounds;
  selectedId: string | null;
  /** Called with a zone id when the user clicks or taps an area. */
  onSelect: (id: string) => void;
  /** Id of the element that explains how to use the map without a pointer. */
  describedBy?: string;
  /** A searched place with no outage data. Drawn as a purple hexagon, and the map moves to it. */
  outlook?: WeatherOutlook | null;
  /** Moves the map to an outage-map area. A new `nonce` moves it again even for the same area. */
  focus?: { id: string; nonce: number } | null;
}

/** Zoom used when the map moves to a searched place: close enough to see the hexagon and the town around it. */
const FOCUS_ZOOM = 10;

const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// GeoJSON order (lng, lat) to Leaflet order (lat, lng).
const toLatLngs = (ring: LngLat[]): L.LatLngTuple[] => ring.map(([lng, lat]) => [lat, lng]);

const bandText = (band: RiskBand | null) => (band ? BAND_LABEL[band] : NOT_RATED_LABEL);
const tipText = (zone: MapZone) =>
  `${zone.name}: ${bandText(zone.band)}${zone.peakGustMph != null ? `, gusts up to ${Math.round(zone.peakGustMph)} mph` : ""}`;

// Build DOM nodes with textContent instead of HTML strings so zone text can never inject markup.
function textNode(className: string, text: string): HTMLElement {
  const el = document.createElement("span");
  el.className = className;
  el.textContent = text;
  return el;
}

function labelIcon(zone: MapZone): L.DivIcon {
  const root = document.createElement("span");
  root.className = "zone-label__inner";
  root.append(textNode("zone-label__name", zone.name), textNode("zone-label__band", zone.band ?? "No data"));
  return L.divIcon({ className: "zone-label", html: root, iconSize: [92, 40], iconAnchor: [46, 20] });
}

function outlookLabelIcon(name: string): L.DivIcon {
  const root = document.createElement("span");
  root.className = "zone-label__inner";
  root.append(textNode("zone-label__name", name), textNode("zone-label__forecast", "Prediction only"));
  return L.divIcon({ className: "zone-label zone-label--forecast", html: root, iconSize: [110, 40], iconAnchor: [55, 20] });
}

/**
 * Map of generalized areas colored by risk band. The map is a pointer shortcut: areas are not
 * individually focusable, so the area picker next to it is the keyboard and screen reader path.
 */
export function ZoneMap({ zones, bounds, selectedId, onSelect, describedBy, outlook, focus }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const outlookLayerRef = useRef<L.LayerGroup | null>(null);
  const outlookBoundsRef = useRef<L.LatLngBounds | null>(null);
  const polygonsRef = useRef(new Map<string, L.Polygon>());
  const labelsRef = useRef(new Map<string, L.Marker>());
  const colorsRef = useRef<Record<string, string>>({});
  const selectedRef = useRef<string | null>(selectedId);
  const onSelectRef = useRef(onSelect);
  const [basemapIssue, setBasemapIssue] = useState(false);

  useEffect(() => {
    onSelectRef.current = onSelect;
    selectedRef.current = selectedId;
  });

  // Create the map once. Bounds are read on first render only; the area set does not move.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const css = getComputedStyle(container);
    const read = (name: string) => css.getPropertyValue(name).trim();
    colorsRef.current = {
      Low: read("--band-low"),
      Medium: read("--band-medium"),
      High: read("--band-high"),
      none: read("--band-none"),
      outline: read("--zone-outline"),
      selected: read("--zone-selected"),
      forecast: read("--forecast") || FORECAST_COLOR_FALLBACK,
    };

    const reduceMotion = prefersReducedMotion();
    const map = L.map(container, {
      attributionControl: false,
      maxZoom: MAX_ZOOM,
      zoomSnap: 0.5,
      zoomAnimation: !reduceMotion,
      fadeAnimation: !reduceMotion,
      markerZoomAnimation: !reduceMotion,
    });

    L.control.attribution({ prefix: "Leaflet" }).addTo(map);

    if (BASEMAP_URL) {
      L.tileLayer(BASEMAP_URL, { maxZoom: MAX_ZOOM, attribution: BASEMAP_ATTRIBUTION })
        .on("tileerror", () => setBasemapIssue(true))
        .on("tileload", () => setBasemapIssue(false))
        .addTo(map);
    }

    // No max bounds: people can pan and zoom out past the areas. Only street-level zoom is capped (MAX_ZOOM).
    map.fitBounds(bounds, { padding: [8, 8] });
    map.setMinZoom(Math.min(MIN_ZOOM, map.getZoom()));

    // Name-only labels when zoomed out, name plus risk word when zoomed in. A class on the container drives the CSS.
    const syncLabels = () => container.classList.toggle("zone-map--far", map.getZoom() < BAND_LABEL_ZOOM);
    map.on("zoomend", syncLabels);
    syncLabels();

    layerRef.current = L.layerGroup().addTo(map);
    outlookLayerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;

    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(container);

    const polygons = polygonsRef.current;
    const labels = labelsRef.current;
    return () => {
      observer.disconnect();
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
      outlookLayerRef.current = null;
      outlookBoundsRef.current = null;
      polygons.clear();
      labels.clear();
    };
  }, []);

  // Add, update and remove area shapes whenever the areas or the selection change.
  useEffect(() => {
    const layer = layerRef.current;
    if (!layer) return;
    const polygons = polygonsRef.current;
    const labels = labelsRef.current;
    const colors = colorsRef.current;

    const styleFor = (zone: MapZone, selected: boolean): L.PathOptions => ({
      stroke: true,
      color: selected ? colors.selected : colors.outline,
      weight: selected ? 4 : 1.5,
      fillColor: zone.band ? colors[zone.band] : colors.none,
      fillOpacity: selected ? 0.62 : zone.band ? 0.48 : 0.3,
      dashArray: zone.band ? undefined : "5 5",
    });

    const seen = new Set<string>();
    for (const zone of zones) {
      seen.add(zone.id);
      const selected = zone.id === selectedId;
      let polygon = polygons.get(zone.id);

      if (!polygon) {
        const created = L.polygon(toLatLngs(zone.ring), styleFor(zone, selected));
        created.on("click", () => onSelectRef.current(zone.id));
        created.on("mouseover", () => {
          if (selectedRef.current !== zone.id) created.setStyle({ weight: 3 });
        });
        created.on("mouseout", () => {
          if (selectedRef.current !== zone.id) created.setStyle({ weight: 1.5 });
        });
        created.bindTooltip(textNode("", tipText(zone)), { sticky: true, direction: "top", className: "zone-tip" });
        created.addTo(layer);
        polygons.set(zone.id, created);
        polygon = created;
      } else {
        polygon.setStyle(styleFor(zone, selected));
        polygon.setTooltipContent(textNode("", tipText(zone)));
      }
      if (selected) polygon.bringToFront();

      const icon = labelIcon(zone);
      const label = labels.get(zone.id);
      if (label) {
        label.setIcon(icon);
      } else {
        const marker = L.marker(polygon.getBounds().getCenter(), { icon, interactive: false, keyboard: false });
        marker.addTo(layer);
        labels.set(zone.id, marker);
      }
    }

    for (const [id, polygon] of polygons) {
      if (seen.has(id)) continue;
      layer.removeLayer(polygon);
      polygons.delete(id);
      const label = labels.get(id);
      if (label) layer.removeLayer(label);
      labels.delete(id);
    }
  }, [zones, selectedId]);

  // Draw the searched place as a purple hexagon and move the map to it. Purple, dashed and labeled "Prediction only",
  // so it can't be mistaken for a rated outage-map area.
  useEffect(() => {
    const map = mapRef.current;
    const layer = outlookLayerRef.current;
    if (!map || !layer) return;
    layer.clearLayers();
    outlookBoundsRef.current = null;
    if (!outlook) return;

    const color = colorsRef.current.forecast;
    const hex = L.polygon(toLatLngs(outlook.area.ring), {
      stroke: true,
      color,
      weight: 3,
      dashArray: "8 6",
      fillColor: color,
      fillOpacity: 0.3,
    });
    hex.bindTooltip(textNode("", `${outlook.place.name}: prediction only, ${outlook.outlook.label} weather level. No outage data.`), {
      sticky: true,
      direction: "top",
      className: "zone-tip",
    });
    hex.addTo(layer);
    L.marker(hex.getBounds().getCenter(), { icon: outlookLabelIcon(outlook.place.name), interactive: false, keyboard: false }).addTo(layer);

    outlookBoundsRef.current = hex.getBounds();
    map.fitBounds(hex.getBounds(), { padding: [40, 40], maxZoom: FOCUS_ZOOM, animate: !prefersReducedMotion() });
  }, [outlook]);

  // Move to an outage-map area the customer searched for.
  useEffect(() => {
    const map = mapRef.current;
    const polygon = focus ? polygonsRef.current.get(focus.id) : undefined;
    if (!map || !polygon) return;
    map.fitBounds(polygon.getBounds(), { padding: [40, 40], maxZoom: FOCUS_ZOOM, animate: !prefersReducedMotion() });
  }, [focus]);

  // Bring a newly chosen area into view when it was picked from the list.
  useEffect(() => {
    const map = mapRef.current;
    const polygon = selectedId ? polygonsRef.current.get(selectedId) : undefined;
    if (!map || !polygon) return;
    const center = polygon.getBounds().getCenter();
    if (!map.getBounds().contains(center)) map.panTo(center, { animate: !prefersReducedMotion() });
  }, [selectedId]);

  return (
    <div className="zone-map">
      <div
        ref={containerRef}
        className="zone-map__canvas"
        role="region"
        aria-label="Map of outage risk by area"
        aria-describedby={describedBy}
      />
      <button
        type="button"
        className="zone-map__reset"
        onClick={() => mapRef.current?.fitBounds(bounds, { padding: [8, 8], animate: !prefersReducedMotion() })}
      >
        Show all areas
      </button>
      {outlook && (
        <button
          type="button"
          className="zone-map__reset zone-map__reset--forecast"
          onClick={() =>
            outlookBoundsRef.current &&
            mapRef.current?.fitBounds(outlookBoundsRef.current, {
              padding: [40, 40],
              maxZoom: FOCUS_ZOOM,
              animate: !prefersReducedMotion(),
            })
          }
        >
          Show {outlook.place.name}
        </button>
      )}
      {basemapIssue && (
        <p className="zone-map__notice" role="status">
          The map background could not load. Areas are still shown.
        </p>
      )}
    </div>
  );
}
