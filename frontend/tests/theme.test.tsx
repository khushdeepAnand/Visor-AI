import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/ThemeProvider";
import { ThemeToggle } from "@/components/ThemeToggle";

function renderToggle() {
  return render(
    <ThemeProvider>
      <ThemeToggle />
    </ThemeProvider>,
  );
}

function matchMediaMock(matchesDark: boolean) {
  return vi.fn().mockImplementation((query: string) => ({
    matches: query.includes("dark") ? matchesDark : !matchesDark,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

describe("ThemeProvider / ThemeToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark");
    window.matchMedia = matchMediaMock(false);
  });

  it("defaults to light when no stored preference and the OS prefers light", async () => {
    renderToggle();
    await waitFor(() => expect(screen.getByRole("button", { name: /switch to dark theme/i })).toBeInTheDocument());
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("reads back a class already applied by the no-flash boot script", async () => {
    // Simulates what THEME_INIT_SCRIPT in app/layout.tsx does before hydration.
    document.documentElement.classList.add("dark");
    renderToggle();
    await waitFor(() => expect(screen.getByRole("button", { name: /switch to light theme/i })).toBeInTheDocument());
  });

  it("toggling switches the class and persists the explicit choice", async () => {
    renderToggle();
    const button = await screen.findByRole("button", { name: /switch to dark theme/i });
    fireEvent.click(button);
    await waitFor(() => expect(document.documentElement.classList.contains("dark")).toBe(true));
    expect(localStorage.getItem("stockpilot-theme")).toBe("dark");
    fireEvent.click(screen.getByRole("button", { name: /switch to light theme/i }));
    await waitFor(() => expect(document.documentElement.classList.contains("dark")).toBe(false));
    expect(localStorage.getItem("stockpilot-theme")).toBe("light");
  });
});
