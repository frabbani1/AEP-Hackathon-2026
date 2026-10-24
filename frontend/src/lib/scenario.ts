import type { DamageKind, DeclaredNeed, Scenario, SeverityPreset } from "../api";

export const SEVERITY_STEPS: ReadonlyArray<{ id: SeverityPreset; label: string; hint: string }> = [
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
export const CREWS_MAX = 8;
export const DAMAGE_MAX = 20;

export const DEFAULT_SCENARIO: Scenario = {
  severity: "typical",
  crews: 2,
  access: "normal",
  upstreamDamage: false,
  damage: null,
};

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
  { id: "accessibility", label: "Someone here needs accessibility support during an outage" },
  { id: "critical-facility", label: "This is a critical facility, such as a clinic, school, or water plant" },
];

export const NEED_LABEL: Record<DeclaredNeed, string> = {
  "medical-power": "Medical equipment",
  accessibility: "Accessibility",
  "critical-facility": "Critical facility",
};
