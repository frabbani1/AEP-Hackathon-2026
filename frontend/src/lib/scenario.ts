import type { DamageKind, DeclaredNeed, Scenario, ScenarioPreset } from "../api";

export const SEVERITY_STEPS: ReadonlyArray<{ id: ScenarioPreset; label: string; hint: string }> = [
  {
    id: "sep-2024-like",
    label: "Like Sep 2024",
    hint: "Like the late September 2024 storm, the smaller of the two past storms we use.",
  },
  {
    id: "typical",
    label: "Typical storm",
    hint: "About the average of the two past storms we use.",
  },
  {
    id: "feb-2025-like",
    label: "Like Feb 2025",
    hint: "Like the mid-February 2025 storm, the larger of the two past storms we use.",
  },
  {
    id: "beyond-observed",
    label: "Beyond past storms",
    hint: "Stronger than any past storm we use. Results here are extrapolations, so confidence is lower.",
  },
];

export const CREWS_MIN = 1;
export const CREWS_MAX = 20;
/** The API takes 1 to 20 of each kind of damage and 1 to 400 repairs in total; the controls stop well short of that. */
export const DAMAGE_MAX = 20;
export const DAMAGE_COUNT_MIN = 1;
export const DAMAGE_COUNT_MAX = 60;

/**
 * Crews match the API's own default. Storm strength starts on live weather, so the first thing a customer sees is
 * what the forecast means for them. If weather cannot load, the API answers with the typical storm and says so.
 */
export const DEFAULT_SCENARIO: Scenario = {
  severity: "weather",
  crews: 3,
  access: "normal",
  damageCount: null,
  damage: null,
};

export const ACCESS_OPTIONS: ReadonlyArray<{ value: Scenario["access"]; label: string }> = [
  { value: "normal", label: "Normal" },
  { value: "restricted", label: "Restricted (blocked roads, hard-to-reach equipment)" },
  { value: "remote", label: "Remote location (far from roads, slow to reach)" },
];

/** Plain-language names for the damage the what-if controls assume. */
export const DAMAGE_LABELS: Record<DamageKind, string> = {
  pole: "Broken poles",
  fuse: "Blown fuses",
  primary: "Main power lines down",
  transformer: "Damaged transformers",
  secondary: "Lines to homes down",
};
export const DAMAGE_ORDER: readonly DamageKind[] = ["pole", "primary", "fuse", "transformer", "secondary"];

export const DECLARED_NEEDS: ReadonlyArray<{ id: DeclaredNeed; label: string }> = [
  { id: "medical-power", label: "Someone here relies on medical equipment that needs power" },
  { id: "accessibility", label: "Someone here has limited mobility and needs support during an outage" },
  { id: "heat-cooling", label: "Someone here depends on electric heating or cooling" },
];

export const NEED_LABEL: Record<DeclaredNeed, string> = {
  "medical-power": "Medical equipment",
  accessibility: "Mobility",
  "heat-cooling": "Heating or cooling",
};
