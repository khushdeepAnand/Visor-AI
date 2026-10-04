"""Live decay tracking for StockPilot AI v13.

Surfaces model decay information to users (not just admins) so they can see
when a model's live performance diverges from backtest expectations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class DecaySignal:
    """A single decay signal for a symbol/horizon."""
    symbol: str
    horizon: int
    signal_type: str
    severity: str
    current_value: float
    expected_range: tuple[float, float]
    description: str
    detected_at: str
    days_since_detection: int


@dataclass(frozen=True, slots=True)
class SymbolDecayStatus:
    """Overall decay status for one symbol."""
    symbol: str
    tier: str
    overall_status: str
    signals: list[DecaySignal]
    live_coverage: float | None
    backtest_coverage: float | None
    live_winkler: float | None
    backtest_winkler: float | None
    live_mase: float | None
    backtest_mase: float | None
    last_updated: str
    auto_widened: bool
    widen_factor: float


def compute_live_vs_backtest_gap(
    live_coverage: float,
    backtest_coverage: float,
    live_winkler: float,
    backtest_winkler: float,
    live_mase: float,
    backtest_mase: float,
    tolerance_coverage: float = 0.10,
    tolerance_winkler: float = 0.20,
    tolerance_mase: float = 0.20,
) -> dict[str, Any]:
    """Compare live metrics to backtest expectations."""
    gaps = {
        "coverage_gap": live_coverage - backtest_coverage,
        "winkler_ratio": live_winkler / backtest_winkler if backtest_winkler > 0 else 1.0,
        "mase_ratio": live_mase / backtest_mase if backtest_mase > 0 else 1.0,
    }

    flags = []
    if abs(gaps["coverage_gap"]) > tolerance_coverage:
        flags.append(f"Coverage gap {gaps['coverage_gap']:.3f} exceeds tolerance {tolerance_coverage}")

    if gaps["winkler_ratio"] > (1 + tolerance_winkler):
        flags.append(f"Winkler score {gaps['winkler_ratio']:.2f}x worse than backtest")

    if gaps["mase_ratio"] > (1 + tolerance_mase):
        flags.append(f"MASE {gaps['mase_ratio']:.2f}x worse than backtest")

    return {
        "gaps": gaps,
        "flags": flags,
        "decay_detected": len(flags) > 0,
        "severity": "high" if len(flags) >= 2 else ("medium" if len(flags) == 1 else "none"),
    }


def detect_decay_signals(
    symbol: str,
    horizon: int,
    tier: str,
    live_data: pd.DataFrame,
    backtest_data: pd.DataFrame,
    *,
    min_live_samples: int = 20,
    coverage_tolerance: float = 0.10,
    winkler_tolerance: float = 0.20,
    mase_tolerance: float = 0.20,
) -> list[DecaySignal]:
    """Detect decay signals by comparing live outcomes to backtest.

    Args:
        live_data: DataFrame with live settled outcomes (columns: actual, low, high, median, confidence)
        backtest_data: DataFrame with backtest outcomes (same columns)
        min_live_samples: Minimum live samples needed for reliable detection
    """
    signals: list[DecaySignal] = []

    if len(live_data) < min_live_samples or len(backtest_data) < 2:
        return signals

    # Compute live metrics
    live_coverage = float(np.mean(
        (live_data["actual"] >= live_data["low"]) & (live_data["actual"] <= live_data["high"])
    ))
    live_winkler = float(np.mean(
        (live_data["high"] - live_data["low"]) +
        np.where(live_data["actual"] < live_data["low"], (2/(1-live_data["confidence"])) * (live_data["low"] - live_data["actual"]), 0) +
        np.where(live_data["actual"] > live_data["high"], (2/(1-live_data["confidence"])) * (live_data["actual"] - live_data["high"]), 0)
    ))
    live_mase = float(np.mean(np.abs(live_data["actual"] - live_data["median"])) /
                      np.mean(np.abs(live_data["actual"].diff().dropna())))

    # Compute backtest metrics
    backtest_coverage = float(np.mean(
        (backtest_data["actual"] >= backtest_data["low"]) & (backtest_data["actual"] <= backtest_data["high"])
    ))
    backtest_winkler = float(np.mean(
        (backtest_data["high"] - backtest_data["low"]) +
        np.where(backtest_data["actual"] < backtest_data["low"], (2/(1-backtest_data["confidence"])) * (backtest_data["low"] - backtest_data["actual"]), 0) +
        np.where(backtest_data["actual"] > backtest_data["high"], (2/(1-backtest_data["confidence"])) * (backtest_data["actual"] - backtest_data["high"]), 0)
    ))
    backtest_mase = float(np.mean(np.abs(backtest_data["actual"] - backtest_data["median"])) /
                          np.mean(np.abs(backtest_data["actual"].diff().dropna())))

    # Compare
    gaps = compute_live_vs_backtest_gap(
        live_coverage, backtest_coverage,
        live_winkler, backtest_winkler,
        live_mase, backtest_mase,
        tolerance_coverage=coverage_tolerance,
        tolerance_winkler=winkler_tolerance,
        tolerance_mase=mase_tolerance,
    )

    if gaps["decay_detected"]:
        for flag in gaps["flags"]:
            if "Coverage gap" in flag:
                signals.append(DecaySignal(
                    symbol=symbol,
                    horizon=horizon,
                    signal_type="coverage_decay",
                    severity=gaps["severity"],
                    current_value=live_coverage,
                    expected_range=(backtest_coverage - 0.05, backtest_coverage + 0.05),
                    description=f"Live coverage {live_coverage:.1%} vs backtest {backtest_coverage:.1%}",
                    detected_at=datetime.now(timezone.utc).isoformat(),
                    days_since_detection=0,
                ))
            elif "Winkler" in flag:
                signals.append(DecaySignal(
                    symbol=symbol,
                    horizon=horizon,
                    signal_type="interval_width_decay",
                    severity=gaps["severity"],
                    current_value=live_winkler,
                    expected_range=(backtest_winkler * 0.8, backtest_winkler * 1.2),
                    description=f"Live Winkler {live_winkler:.2f} vs backtest {backtest_winkler:.2f}",
                    detected_at=datetime.now(timezone.utc).isoformat(),
                    days_since_detection=0,
                ))
            elif "MASE" in flag:
                signals.append(DecaySignal(
                    symbol=symbol,
                    horizon=horizon,
                    signal_type="accuracy_decay",
                    severity=gaps["severity"],
                    current_value=live_mase,
                    expected_range=(backtest_mase * 0.8, backtest_mase * 1.2),
                    description=f"Live MASE {live_mase:.2f} vs backtest {backtest_mase:.2f}",
                    detected_at=datetime.now(timezone.utc).isoformat(),
                    days_since_detection=0,
                ))

    return signals


def build_symbol_decay_status(
    symbol: str,
    tier: str,
    live_data: pd.DataFrame,
    backtest_data: pd.DataFrame,
    *,
    min_live_samples: int = 20,
    auto_widen_threshold: float = 0.15,
) -> SymbolDecayStatus:
    """Build complete decay status for a symbol."""
    signals = detect_decay_signals(symbol, 1, tier, live_data, backtest_data, min_live_samples=min_live_samples)
    enough_evidence = len(live_data) >= min_live_samples and len(backtest_data) >= 2

    # Compute overall metrics
    live_coverage = float(np.mean(
        (live_data["actual"] >= live_data["low"]) & (live_data["actual"] <= live_data["high"])
    )) if len(live_data) > 0 else None

    backtest_coverage = float(np.mean(
        (backtest_data["actual"] >= backtest_data["low"]) & (backtest_data["actual"] <= backtest_data["high"])
    )) if len(backtest_data) > 0 else None

    # Check if auto-widen triggered
    auto_widened = False
    widen_factor = 1.0
    if enough_evidence and live_coverage is not None and backtest_coverage is not None:
        gap = backtest_coverage - live_coverage
        if gap > auto_widen_threshold:
            auto_widened = True
            widen_factor = 1.0 + gap * 2  # Widen proportionally

    # Determine overall status
    if not enough_evidence:
        overall_status = "insufficient_evidence"
    elif not signals:
        overall_status = "healthy"
    elif any(s.severity == "high" for s in signals):
        overall_status = "degraded"
    else:
        overall_status = "watch"

    return SymbolDecayStatus(
        symbol=symbol,
        tier=tier,
        overall_status=overall_status,
        signals=signals,
        live_coverage=live_coverage,
        backtest_coverage=backtest_coverage,
        live_winkler=None,  # Would need to compute
        backtest_winkler=None,
        live_mase=None,
        backtest_mase=None,
        last_updated=datetime.now(timezone.utc).isoformat(),
        auto_widened=auto_widened,
        widen_factor=widen_factor,
    )


def decay_status_to_dict(status: SymbolDecayStatus) -> dict[str, Any]:
    """Convert decay status to user-facing dictionary."""
    return {
        "symbol": status.symbol,
        "tier": status.tier,
        "overall_status": status.overall_status,
        "live_coverage": status.live_coverage,
        "backtest_coverage": status.backtest_coverage,
        "auto_widened": status.auto_widened,
        "widen_factor": round(status.widen_factor, 2),
        "signals": [
            {
                "type": s.signal_type,
                "severity": s.severity,
                "current_value": round(s.current_value, 4),
                "expected_range": [round(s.expected_range[0], 4), round(s.expected_range[1], 4)],
                "description": s.description,
                "detected_at": s.detected_at,
                "days_since_detection": s.days_since_detection,
            }
            for s in status.signals
        ],
        "last_updated": status.last_updated,
        "disclosure": (
            "Live decay tracking compares actual model performance to backtest expectations. "
            "Widening recommendations require enforcement by the forecast pipeline; "
            "this diagnostic alone does not change a published interval."
        ),
    }


def get_user_facing_decay_card(
    symbol: str,
    tier: str,
    live_data: pd.DataFrame,
    backtest_data: pd.DataFrame,
) -> dict[str, Any]:
    """Generate a user-facing decay card for the analysis UI."""
    status = build_symbol_decay_status(symbol, tier, live_data, backtest_data)
    base = decay_status_to_dict(status)

    # Add plain-language summary
    if status.overall_status == "insufficient_evidence":
        base["summary"] = f"{symbol} has too few settled outcomes to judge live reliability."
    elif status.overall_status == "healthy":
        base["summary"] = f"{symbol} model is performing in line with backtest expectations."
    elif status.overall_status == "watch":
        base["summary"] = f"{symbol} model shows minor deviations from backtest. Monitoring closely."
    else:
        base["summary"] = f"{symbol} model has degraded. Review or wider intervals are recommended."

    if status.auto_widened:
        base["recommended_action"] = (
            f"Widen intervals by {status.widen_factor - 1:.0%}; "
            f"verify subsequent coverage before claiming improved reliability."
        )

    return base


__all__: Sequence[str] = (
    "DecaySignal",
    "SymbolDecayStatus",
    "compute_live_vs_backtest_gap",
    "detect_decay_signals",
    "build_symbol_decay_status",
    "decay_status_to_dict",
    "get_user_facing_decay_card",
)
