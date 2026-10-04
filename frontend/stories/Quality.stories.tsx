import { CORE_WEB_VITAL_BUDGETS } from "@/lib/quality";

function QualityContract() {
  return <section aria-labelledby="quality-title"><h2 id="quality-title">Quality contract</h2><p>Keyboard accessible UI with explicit data freshness.</p><dl><dt>LCP</dt><dd>{CORE_WEB_VITAL_BUDGETS.lcpMs}ms</dd><dt>INP</dt><dd>{CORE_WEB_VITAL_BUDGETS.inpMs}ms</dd><dt>CLS</dt><dd>{CORE_WEB_VITAL_BUDGETS.cls}</dd></dl></section>;
}

// Storybook CSF metadata. Kept dependency-free so the repository's normal
// Next/Vitest checks do not require Storybook to be installed.
const meta = { title: "Quality/Contract", component: QualityContract, parameters: { a11y: { disable: false } } };
export default meta;
export const Default = {};
