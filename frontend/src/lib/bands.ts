import type { RiskBand } from "../api";

/** Display copy for risk bands. Order is lowest to highest. */
export const BANDS: readonly RiskBand[] = ["Low", "Medium", "High"];

export const BAND_LABEL: Record<RiskBand, string> = {
  Low: "Low risk",
  Medium: "Medium risk",
  High: "High risk",
};

/** Ordinal wording only. The bands rank areas; they are not probabilities. */
export const BAND_SUMMARY: Record<RiskBand, string> = {
  Low: "A lower chance of an outage in this scenario.",
  Medium: "A moderate chance of an outage in this scenario.",
  High: "A higher chance of an outage in this scenario.",
};

/** Filled bars of three, so the level never depends on color alone. */
export const BAND_BARS: Record<RiskBand, number> = { Low: 1, Medium: 2, High: 3 };

export const NOT_RATED_LABEL = "Not enough data";
