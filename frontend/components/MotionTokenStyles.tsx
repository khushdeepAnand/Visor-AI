import { MOTION_DURATIONS_MS, MOTION_EASINGS, MOTION_TRANSLATE_PX } from "@/lib/motionTokens";

/**
 * Publishes MOTION_TOKENS to CSS as custom properties.
 *
 * `globals.css` cannot import TypeScript, so before this component existed the
 * stylesheet carried its own hard-coded durations and cubic-beziers, which is
 * exactly the duplication MOTION_SYSTEM.md forbids. This server component emits
 * the token values once in the document head, so CSS and components read the
 * same numbers from one source of truth.
 */
export function MotionTokenStyles() {
  const css = [
    ":root {",
    `  --motion-instant: ${MOTION_DURATIONS_MS.instant}ms;`,
    `  --motion-micro: ${MOTION_DURATIONS_MS.micro}ms;`,
    `  --motion-interaction: ${MOTION_DURATIONS_MS.interaction}ms;`,
    `  --motion-page: ${MOTION_DURATIONS_MS.page}ms;`,
    `  --motion-corridor-reveal: ${MOTION_DURATIONS_MS.corridorReveal}ms;`,
    `  --motion-price-tint: ${MOTION_DURATIONS_MS.priceTint}ms;`,
    `  --motion-ease-standard: ${MOTION_EASINGS.standard};`,
    `  --motion-ease-decelerate: ${MOTION_EASINGS.decelerate};`,
    `  --motion-ease-accelerate: ${MOTION_EASINGS.accelerate};`,
    `  --motion-ease-emphasized: ${MOTION_EASINGS.emphasized};`,
    `  --motion-translate-page: ${MOTION_TRANSLATE_PX.page}px;`,
    `  --motion-translate-card-hover: ${MOTION_TRANSLATE_PX.cardHover}px;`,
    `  --motion-translate-tooltip: ${MOTION_TRANSLATE_PX.tooltip}px;`,
    "}",
  ].join("\n");
  return <style id="motion-tokens" dangerouslySetInnerHTML={{ __html: css }} />;
}

export default MotionTokenStyles;
