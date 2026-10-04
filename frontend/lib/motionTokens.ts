/**
 * MOTION_TOKENS - the single source of truth for StockPilot animation.
 *
 * Intended repository path: `frontend/lib/motionTokens.ts`.
 *
 * The audit prompt requires a shared `MOTION_TOKENS` definition for durations,
 * easings, springs, stagger, and reduced-motion fallbacks, reused everywhere
 * instead of embedding random animation values in components. `MOTION_SYSTEM.md`
 * documents the policy; this file makes it executable and testable.
 *
 * Rules encoded here:
 * - motion explains state changes; it never decorates uncertainty as certainty;
 * - no looping or celebratory animation is available as a token;
 * - every travel/scale token has a reduced-motion fallback that keeps the state
 *   cue and only removes movement;
 * - only `transform` and `opacity` are animated, so 60fps is achievable on the
 *   target Windows hardware.
 */

export type MotionDurationToken =
  | "instant"
  | "micro"
  | "interaction"
  | "page"
  | "corridorReveal"
  | "priceTint";

export const MOTION_DURATIONS_MS: Record<MotionDurationToken, number> = {
  instant: 0,
  micro: 120,
  interaction: 180,
  page: 220,
  corridorReveal: 520,
  priceTint: 360,
};

export const MOTION_EASINGS = {
  standard: "cubic-bezier(0.2, 0, 0.2, 1)",
  decelerate: "cubic-bezier(0.05, 0.7, 0.1, 1)",
  accelerate: "cubic-bezier(0.3, 0, 1, 1)",
  emphasized: "cubic-bezier(0.2, 0, 0, 1)",
} as const;

export const MOTION_SPRINGS = {
  /** Damped, non-overshooting reveal used by the Forecast Corridor. */
  corridor: { stiffness: 170, damping: 26, mass: 1 },
  /** Small confirmation spring for save/watch toggles. */
  confirm: { stiffness: 320, damping: 30, mass: 0.7 },
} as const;

export const MOTION_STAGGER_MS = {
  /** Only the first visible card group may stagger. */
  firstCardGroup: 40,
  maxStaggeredItems: 6,
  /** Tables and long lists must never be staggered. */
  tables: 0,
} as const;

export const MOTION_TRANSLATE_PX = {
  page: 10,
  cardHover: 3,
  tooltip: 6,
} as const;

/** Effects that are explicitly not available as tokens. */
export const PROHIBITED_MOTION = [
  "confetti",
  "looping-glow",
  "shake",
  "flash",
  "animated-price-path",
  "3d-tilt",
  "parallax-scroll",
  "large-blur-transition",
] as const;

export type ProhibitedMotion = (typeof PROHIBITED_MOTION)[number];

export type MotionPreference = "full" | "reduced";

export type MotionSpec = {
  durationMs: number;
  easing: string;
  translatePx: number;
  /** Properties allowed to animate; anything else is a performance bug. */
  properties: ReadonlyArray<"opacity" | "transform">;
};

const REDUCED_FALLBACK_MS = 120;

/**
 * Resolve a motion spec for the current user preference.
 *
 * Under `reduced`, travel is removed and the duration collapses to a short
 * fade, but the animation is never dropped entirely, so the state change is
 * still perceivable.
 */
export function resolveMotion(
  token: MotionDurationToken,
  preference: MotionPreference = "full",
  options: { translatePx?: number; easing?: string } = {},
): MotionSpec {
  const easing = options.easing ?? MOTION_EASINGS.standard;
  const translatePx = options.translatePx ?? 0;
  if (preference === "reduced") {
    return {
      durationMs: Math.min(REDUCED_FALLBACK_MS, MOTION_DURATIONS_MS[token] || REDUCED_FALLBACK_MS),
      easing: MOTION_EASINGS.standard,
      translatePx: 0,
      properties: ["opacity"],
    };
  }
  return {
    durationMs: MOTION_DURATIONS_MS[token],
    easing,
    translatePx,
    properties: translatePx === 0 ? ["opacity"] : ["opacity", "transform"],
  };
}

/** Stagger delay for index `i`, capped so long lists are never delayed. */
export function staggerDelayMs(index: number, preference: MotionPreference = "full"): number {
  if (preference === "reduced" || index < 0) return 0;
  if (index >= MOTION_STAGGER_MS.maxStaggeredItems) return 0;
  return index * MOTION_STAGGER_MS.firstCardGroup;
}

/** CSS transition string built only from tokens. */
export function transitionCss(
  token: MotionDurationToken,
  preference: MotionPreference = "full",
  options: { translatePx?: number; easing?: string } = {},
): string {
  const spec = resolveMotion(token, preference, options);
  return spec.properties.map((property) => `${property} ${spec.durationMs}ms ${spec.easing}`).join(", ");
}

/** Detect the user preference in the browser; SSR-safe. */
export function motionPreference(): MotionPreference {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return "full";
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "reduced" : "full";
}

export const MOTION_TOKENS = {
  durations: MOTION_DURATIONS_MS,
  easings: MOTION_EASINGS,
  springs: MOTION_SPRINGS,
  stagger: MOTION_STAGGER_MS,
  translate: MOTION_TRANSLATE_PX,
  prohibited: PROHIBITED_MOTION,
  resolveMotion,
  staggerDelayMs,
  transitionCss,
  motionPreference,
} as const;

export default MOTION_TOKENS;
