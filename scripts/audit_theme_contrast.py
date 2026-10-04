"""WCAG AA contrast audit for both Ledger themes (Part L4).

Reads the CSS-variable token definitions from ``frontend/app/globals.css``,
computes WCAG relative luminance / contrast ratios for the ramp treated as
text tokens (``--slate-400/500/600`` and ``--content-strong``) against the
paper/sunken surfaces (``--terminal-950/900/850``) in both the light ``:root``
and ``html.dark`` blocks, and fails if any body-text token falls under
4.5:1 (AA) or under 3.0:1 for large text.

Usage:  python scripts/audit_theme_contrast.py
Exit 0 = every audited pairing passes AA.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "frontend" / "app" / "globals.css"

AA_BODY = 4.5
AA_LARGE = 3.0

TEXT_TOKENS = ("--slate-400", "--slate-500", "--slate-600", "--content-strong")
BACKGROUND_TOKENS = ("--terminal-950", "--terminal-900", "--terminal-850")
# --slate-400/500/600 are labels/muted text (small), content-strong is larger.
LARGE = {"--content-strong"}


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(channel: float) -> float:
        scaled = channel / 255.0
        return scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(value) for value in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    lighter = max(relative_luminance(hex_to_rgb(fg)), relative_luminance(hex_to_rgb(bg)))
    darker = min(relative_luminance(hex_to_rgb(fg)), relative_luminance(hex_to_rgb(bg)))
    return (lighter + 0.05) / (darker + 0.05)


def block_start(lines: list[str], index: int) -> bool:
    return lines[index].lstrip().startswith(":root") or lines[index].lstrip().startswith("html.dark")


def extract_blocks(text: str) -> dict[str, dict[str, str]]:
    tokens: dict[str, dict[str, str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if current is None:
            if re.match(r"^:root\s*\{", stripped):
                current = "light"
                tokens.setdefault(current, {})
                continue
            if re.match(r"^html\.dark\s*\{", stripped):
                current = "dark"
                tokens.setdefault(current, {})
                continue
            continue
        if stripped == "}":
            current = None
            continue
        match = re.match(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})\b", stripped)
        if match:
            tokens[current][match.group(1)] = match.group(2)
    return tokens


def main() -> int:
    text = CSS.read_text(encoding="utf-8")
    blocks = extract_blocks(text)
    failed = False
    for theme in ("light", "dark"):
        tokens = blocks.get(theme, {})
        if not tokens:
            print(f"[{theme}] no token block found")
            failed = True
            continue
        print(f"\n=== {theme} theme ===")
        for foreground in TEXT_TOKENS:
            if foreground not in tokens:
                print(f"  {foreground}: MISSING")
                failed = True
                continue
            for background in BACKGROUND_TOKENS:
                if background not in tokens:
                    continue
                ratio = contrast_ratio(tokens[foreground], tokens[background])
                threshold = AA_LARGE if foreground in LARGE else AA_BODY
                status = "PASS" if ratio >= threshold else "FAIL"
                if status == "FAIL":
                    failed = True
                print(
                    f"  {foreground} on {background} ({tokens[foreground]} / {tokens[background]}): "
                    f"{ratio:.2f}:1 {status} (AA{' body 4.5' if threshold == AA_BODY else ' large 3.0'})"
                )
    print("\nVerdict:", "FAIL - adjust tokens above to meet WCAG AA." if failed else "ALL PAIRINGS PASS WCAG AA")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())