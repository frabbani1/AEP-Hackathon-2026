import type { LatLngBounds, LngLat } from "../types";

/**
 * PLACEHOLDER geography and history for the map preview. None of this is real.
 *
 * The 30 areas are a regular hexagon grid laid over a rounded version of the study
 * area's overall extent (about 37.0 to 37.5 N, 79.6 to 80.3 W), so the map opens in the
 * right region. The real API will return generalized zones built from the curated data;
 * each one must cover at least `MIN_HOMES_PER_AREA` homes.
 */

export const MIN_HOMES_PER_AREA = 10;

export const SAMPLE_BOUNDS: LatLngBounds = [
  [37.0, -80.25],
  [37.48, -79.65],
];

export type ServiceMix = "overhead" | "underground" | "mixed";
export type FallbackReason = "circuit_too_small" | "no_history_small_circuit";

export interface SampleZone {
  id: string;
  name: string;
  ring: LngLat[];
  /** Placeholder susceptibility. Higher means more storm damage history. */
  base: number;
  /** Damage reports behind the rating. Drives confidence. */
  records: number;
  /** Protective devices between the area and the power source. */
  devices: number;
  service: ServiceMix;
  facility: boolean;
  fallback: FallbackReason | null;
}

type Spec = readonly [
  base: number,
  records: number,
  devices: number,
  service: ServiceMix,
  facility?: boolean,
  fallback?: FallbackReason,
];

// Row-major, 5 areas per row, 6 rows. Includes a few hot spots, a few quiet underground
// areas, and two areas with too little history so the fallback can be demonstrated.
const SPECS: readonly Spec[] = [
  [0.35, 3, 2, "underground"], [0.55, 8, 3, "overhead"], [0.7, 14, 4, "overhead"], [0.95, 22, 6, "overhead"], [0.6, 9, 3, "mixed"],
  [0.3, 2, 2, "underground"], [1.3, 31, 7, "overhead", true], [1.05, 26, 6, "overhead"], [0.5, 6, 3, "mixed"], [0, 0, 0, "mixed", false, "circuit_too_small"],
  [0.85, 18, 5, "overhead"], [1.6, 38, 8, "overhead", true], [1.2, 24, 6, "overhead"], [0.4, 4, 2, "underground"], [0.75, 11, 4, "mixed"],
  [0.25, 1, 1, "underground"], [0.65, 7, 3, "mixed"], [1.1, 20, 5, "overhead", true], [0.9, 15, 5, "overhead"], [0, 0, 0, "mixed", false, "no_history_small_circuit"],
  [0.45, 5, 2, "mixed"], [0.8, 12, 4, "overhead"], [1.4, 29, 7, "overhead"], [0.55, 6, 3, "mixed"], [0.35, 3, 2, "underground"],
  [0.6, 8, 3, "mixed"], [0.95, 17, 5, "overhead", true], [0.7, 10, 4, "overhead"], [0.3, 2, 1, "underground"], [0.5, 5, 3, "mixed"],
];

const COLUMNS = 5;
const ROWS = 6;
const CENTER = { lat: 37.24, lng: -79.95 };
const RADIUS_M = 5500; // hexagon circumradius
const M_PER_DEG_LAT = 111_200;
const M_PER_DEG_LNG = 111_320 * Math.cos((CENTER.lat * Math.PI) / 180);

function hexRing(cxM: number, cyM: number): LngLat[] {
  const ring: LngLat[] = [];
  for (let k = 0; k < 6; k += 1) {
    const angle = ((30 + 60 * k) * Math.PI) / 180; // pointy-top
    ring.push([
      CENTER.lng + (cxM + RADIUS_M * Math.cos(angle)) / M_PER_DEG_LNG,
      CENTER.lat + (cyM + RADIUS_M * Math.sin(angle)) / M_PER_DEG_LAT,
    ]);
  }
  return ring;
}

function buildZones(): SampleZone[] {
  const width = Math.sqrt(3) * RADIUS_M;
  const spanX = (COLUMNS - 1 + 0.5) * width;
  const spanY = (ROWS - 1) * 1.5 * RADIUS_M;

  return SPECS.map((spec, index) => {
    const [base, records, devices, service, facility = false, fallback = null] = spec;
    const row = Math.floor(index / COLUMNS);
    const col = index % COLUMNS;
    const cx = (col + (row % 2) * 0.5) * width - spanX / 2;
    // Row 0 sits at the north edge.
    const cy = spanY / 2 - row * 1.5 * RADIUS_M;
    return {
      id: `sample-${String(index + 1).padStart(2, "0")}`,
      name: `Area ${index + 1}`,
      ring: hexRing(cx, cy),
      base,
      records,
      devices,
      service,
      facility,
      fallback,
    };
  });
}

export const SAMPLE_ZONES: readonly SampleZone[] = buildZones();
