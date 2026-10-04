/** Small, dependency-free quality gates shared by Storybook smoke checks. */
export const CORE_WEB_VITAL_BUDGETS = {
  lcpMs: 2500,
  inpMs: 200,
  cls: 0.1,
} as const;

export type VitalSample = { lcpMs?: number; inpMs?: number; cls?: number };

export function withinCoreWebVitalBudgets(sample: VitalSample): boolean {
  return (sample.lcpMs === undefined || sample.lcpMs <= CORE_WEB_VITAL_BUDGETS.lcpMs)
    && (sample.inpMs === undefined || sample.inpMs <= CORE_WEB_VITAL_BUDGETS.inpMs)
    && (sample.cls === undefined || sample.cls <= CORE_WEB_VITAL_BUDGETS.cls);
}

/** Minimal a11y contract for stories: every form control must have a label. */
export function hasAccessibleFormLabels(root: ParentNode): boolean {
  return [...root.querySelectorAll("input,select,textarea")].every((control) => {
    const id = control.getAttribute("id");
    return Boolean(id && root.querySelector(`label[for="${CSS.escape(id)}"]`));
  });
}
