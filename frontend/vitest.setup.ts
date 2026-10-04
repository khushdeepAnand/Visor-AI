import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

let unexpectedConsoleMessages: string[] = [];

function captureConsole(level: "error" | "warn", args: unknown[]) {
  const message = args
    .map((value) => value instanceof Error ? value.stack || value.message : String(value))
    .join(" ");
  unexpectedConsoleMessages.push(`console.${level}: ${message}`);
}

beforeEach(() => {
  unexpectedConsoleMessages = [];
  vi.spyOn(console, "error").mockImplementation((...args) => captureConsole("error", args));
  vi.spyOn(console, "warn").mockImplementation((...args) => captureConsole("warn", args));
});

afterEach(() => {
  cleanup();
  const messages = unexpectedConsoleMessages;
  vi.restoreAllMocks();
  if (messages.length > 0) {
    throw new Error(`Unexpected console output:\n${messages.join("\n")}`);
  }
});

// jsdom performs no layout, so @tanstack/react-virtual (and any other
// ResizeObserver-driven UI) sees 0-height containers and renders no rows.
// Give elements a plausible viewport size and a ResizeObserver stub so
// virtualized/measured components behave the way they do in a real browser.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
global.ResizeObserver = global.ResizeObserver ?? ResizeObserverStub;

Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: 400 });
Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, value: 800 });
Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, value: 400 });
Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, value: 800 });

// jsdom does not implement matchMedia. ThemeProvider calls it to resolve the
// OS light/dark preference; without a stub every page that renders the theme
// toggle throws "window.matchMedia is not a function". Return a non-matching
// list so tests exercise a deterministic default.
Object.defineProperty(window, "matchMedia", {
  configurable: true,
  writable: true,
  value: (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList,
});
