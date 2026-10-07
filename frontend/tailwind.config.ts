import type { Config } from "tailwindcss";
export default {
  darkMode: ["class", ".dark"],
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        white: "var(--content-strong)",
        slate: {
          50: "var(--slate-50)", 100: "var(--slate-100)", 200: "var(--slate-200)", 300: "var(--slate-300)", 400: "var(--slate-400)",
          500: "var(--slate-500)", 600: "var(--slate-600)", 700: "var(--slate-700)", 800: "var(--slate-800)", 900: "var(--slate-900)", 950: "var(--slate-950)"
        },
        terminal: { 950: "var(--terminal-950)", 900: "var(--terminal-900)", 850: "var(--terminal-850)", 800: "var(--terminal-800)" },
        accent: { DEFAULT: "var(--accent-primary)", soft: "var(--accent-soft)" },
        secondary: "var(--accent-secondary)",
        info: "var(--semantic-info)",
        warning: "var(--semantic-warning)",
        gain: "var(--semantic-gain)",
        loss: "var(--semantic-loss)"
      },
      fontFamily: { mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"], display: ["var(--font-display)", "Inter", "ui-sans-serif", "sans-serif"] },
      boxShadow: { panel: "var(--shadow-panel)" }
    }
  },
  plugins: []
} satisfies Config;
