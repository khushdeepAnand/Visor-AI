import { describe, expect, it } from "vitest";
import { hasAccessibleFormLabels, withinCoreWebVitalBudgets } from "@/lib/quality";

describe("frontend quality contracts", () => {
  it("rejects regressions beyond Core Web Vital budgets", () => {
    expect(withinCoreWebVitalBudgets({ lcpMs: 2499, inpMs: 199, cls: 0.09 })).toBe(true);
    expect(withinCoreWebVitalBudgets({ lcpMs: 2501 })).toBe(false);
  });
  it("requires labels for form controls", () => {
    const root = document.createElement("div");
    root.innerHTML = '<label for="symbol">Symbol</label><input id="symbol">';
    expect(hasAccessibleFormLabels(root)).toBe(true);
    root.innerHTML = '<input id="symbol">';
    expect(hasAccessibleFormLabels(root)).toBe(false);
  });
});
