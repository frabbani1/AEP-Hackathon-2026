import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef, useState } from "react";
import type { LatLngBounds, LngLat, MapZone, RiskBand } from "../../api";
import { BAND_LABEL, NOT_RATED_LABEL } from "../../lib/bands";
import "./ZoneMap.css";

/**
 * Background tiles. Set VITE_BASEMAP_URL to another tile template, or to an empty string to
 * draw the areas on a plain background with no third-party requests. Tile requests tell the
 * tile host which part of the map is in view, so the zoom is capped (MAX_ZOOM) well short
 * of street level.
 */
const DEFAULT_BASEMAP_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const DEFAULT_BASEMAP_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors';
const BASEMAP_URL: string = import.meta.env.VITE_BASEMAP_URL ?? DEFAULT_BASEMAP_URL;
const BASEMAP_ATTRIBUTION: string = import.meta.env.VITE_BASEMAP_ATTRIBUTION ?? DEFAULT_BASEMAP_ATTRIBUTION;

const MAX_ZOOM = 12;

interface Props {
  zones: MapZone[];
  bounds: LatLngBounds;
  selectedId: string | null;
  /** Called with a zone id when the user clicks or taps an area. */
  onSelect: (id: string) => void;
  /** Id of the element that explains how to use the map without a pointer. */
  describedBy?: string;
}

const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// GeoJSON order (lng, lat) to Leaflet order (lat, lng).
const toLatLngs = (ring: LngLat[]): L.LatLngTuple[] => ring.map(([lng, lat]) => [lat, lng]);

const bandText = (band: RiskBand | null) => (band ? BAND_LABEL[band] : NOT_RATED_LABEL);

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
  root.append(textNode("zone-label__name", zone.name.replace(/^Area\s+/, "")), textNode("zone-label__band", zone.band ?? "No data"));
  return L.divIcon({ className: "zone-label", html: root, iconSize: [64, 36], iconAnchor: [32, 18] });
}

/**
 * Map of generalized areas colored by risk band. The map is a pointer shortcut: areas are not
 * individually focusable, so the area picker next to it is the keyboard and screen reader path.
 */
export function ZoneMap({ zones, bounds, selectedId, onSelect, describedBy }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
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

    L.control
      .attribution({ prefix: '<a href="https://leafletjs.com" target="_blank" rel="noopener noreferrer">Leaflet</a>' })
      .addTo(map);

    if (BASEMAP_URL) {
      L.tileLayer(BASEMAP_URL, { maxZoom: MAX_ZOOM, attribution: BASEMAP_ATTRIBUTION })
        .on("tileerror", () => setBasemapIssue(true))
        .on("tileload", () => setBasemapIssue(false))
        .addTo(map);
    }

    map.fitBounds(bounds, { padding: [8, 8] });
    map.setMinZoom(Math.max(map.getZoom() - 1, 0));
    map.setMaxBounds(L.latLngBounds(bounds).pad(0.5));

    layerRef.current = L.layerGroup().addTo(map);
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
        created.bindTooltip(textNode("", `${zone.name}: ${bandText(zone.band)}`), { sticky: true, direction: "top", className: "zone-tip" });
        created.addTo(layer);
        polygons.set(zone.id, created);
        polygon = created;
      } else {
        polygon.setStyle(styleFor(zone, selected));
        polygon.setTooltipContent(textNode("", `${zone.name}: ${bandText(zone.band)}`));
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
      {basemapIssue && (
        <p className="zone-map__notice" role="status">
          The map background could not load. Areas are still shown.
        </p>
      )}
    </div>
  );
}
