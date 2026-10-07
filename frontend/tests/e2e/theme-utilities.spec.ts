import { expect, test } from "@playwright/test";

test("theme tokens and layout utilities survive the CSS toolchain upgrade", async ({ page }) => {
  await page.goto("/login");
  const styles = await page.evaluate(() => {
    const probe = document.createElement("div");
    probe.className = "flex gap-4 p-4 bg-terminal-900 text-slate-200 border border-slate-700";
    document.body.append(probe);
    const sample = () => {
      const style = getComputedStyle(probe);
      return { display: style.display, gap: style.gap, padding: style.padding,
        background: style.backgroundColor, color: style.color, border: style.borderTopWidth };
    };
    document.documentElement.classList.remove("dark");
    const light = sample();
    document.documentElement.classList.add("dark");
    const dark = sample();
    probe.remove();
    return { light, dark };
  });
  expect(styles.light).toMatchObject({ display: "flex", gap: "16px", padding: "16px",
    background: "rgb(253, 251, 245)", color: "rgb(36, 31, 22)", border: "1px" });
  expect(styles.dark).toMatchObject({ background: "rgb(26, 23, 15)", color: "rgb(221, 212, 187)" });
});
