# StockPilot AI Design System

## Direction

The interface uses the existing precise terminal language with calmer composition: deep or pale neutral surfaces, emerald action accents, indigo research accents, restrained borders, tabular numbers, and minimal glow. Dense mosaic composition is reserved for Pro mode.

## Semantic Tokens

Tokens are defined in `frontend/app/globals.css` and consumed through `frontend/tailwind.config.ts`.

| Role | Dark | Light |
| --- | --- | --- |
| Canvas | `--terminal-950: #05070d` | `--terminal-950: #f4f7fb` |
| Surface | `--terminal-900: #0a0e16` | `--terminal-900: #ffffff` |
| Strong text | `--content-strong: #f8fafc` | `--content-strong: #0f172a` |
| Action / focus | `--accent-primary: #34d399` | `--accent-primary: #047857` |
| Research | `--accent-secondary: #7d8cff` | `--accent-secondary: #4f46e5` |
| Information | `--semantic-info: #5aa7ff` | `--semantic-info: #1d4ed8` |
| Warning | `--semantic-warning: #f0b64d` | `--semantic-warning: #8a5a00` |
| Loss / failure | `--semantic-loss: #f7657a` | `--semantic-loss: #be123c` |

The slate scale is role-inverted in light mode so existing semantic utility usage remains readable. The system follows `prefers-color-scheme`; no dependency or theme flash script is required.

## Typography

- Product and section headings: `--font-display`, Space Grotesk with system fallback.
- Body: Inter with system fallback.
- Prices, percentages, and symbols: tabular system monospace.
- Eyebrows: 10px uppercase with restrained tracking.
- Body copy: 14-16px and 1.5 line height for explanatory text.

## Layout

- Spacing follows a 4px base with common steps at 8, 12, 16, 20, 24, 32, and 40px.
- Cards use 12-16px internal padding and 8-16px gaps.
- Primary content is capped at 1800px.
- Header is 64px; desktop side navigation is 192px.
- Corners are 8px for controls, 12px for cards, and 16px for major surfaces/dialogs.

## Components

- Primary and ghost buttons have a minimum 44px height.
- Inputs and selects are 44px high.
- Primary navigation labels are Home, Explore, Paper Trade, Portfolio, and More.
- Status pills always pair color with visible text and, for critical forecast state, an icon.
- Forecast Corridor uses the interval as the strongest numeric element.
- Trust Stack uses four concise cells: support state, model path, evidence, and data state.

## Content Rules

- Say "forecast corridor" or "research range", not "price target".
- Say "analytical scenario" for derivatives output.
- Say "not reported" or "unavailable" for missing operational telemetry.
- Never infer live, healthy, or validated from missing fields. Legacy forecasts map their explicit confidence and model labels into the four support states until the job contract supplies `support_state` directly.
- Do not use gain/loss color without an Up/Down, Success/Failed, or equivalent text cue.
