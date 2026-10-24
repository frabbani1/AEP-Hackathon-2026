/** Mirrors RESULT_SCHEMA in backend/concierge/risk/schema.py. The API returns nothing outside this shape. */

export interface Factor {
  id: string;
  direction: 'raises' | 'lowers';
  weight: number;
  text: string;
}

export interface RiskResult {
  engine_version: string;
  config_hash: string;
  premise_token: string | null;
  status: 'ok' | 'insufficient_data';
  label: string | null;
  severity: { preset: string; multiplier: number; extrapolated: boolean } | null;
  band: 'Low' | 'Medium' | 'High' | null;
  score: number | null;
  confidence: { level: 'Low' | 'Medium' | 'High'; records: number; reason: string } | null;
  band_evidence: {
    backtest: string;
    hit_rate: number;
    ci95: [number, number];
    n_circuits: number;
    hit_definition: string;
    sentence: string;
  } | null;
  top_factors: Factor[];
  signals: { circuit_reports_per_1k: number; protective_devices: number } | null;
  assumptions: string[];
  missing_data: string[];
  could_change: string[];
  fallback: { reason_code: string; message: string } | null;
}

/** Storm scenarios the risk engine accepts by name (calibration.json "presets"). */
export const SCENARIOS = [
  { value: 'sep-2024-like', label: 'Milder than usual (like September 2024)' },
  { value: 'typical', label: 'Typical storm' },
  { value: 'feb-2025-like', label: 'Severe (like February 2025)' },
  { value: 'beyond-observed', label: 'Stronger than any storm on record' },
] as const;
