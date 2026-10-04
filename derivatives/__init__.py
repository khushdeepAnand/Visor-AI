"""Credential-free derivatives analytics primitives."""

from derivatives.futures_engine import analyze_futures_contract, classify_open_interest
from derivatives.options_engine import analyze_option_chain, analyze_option_scenarios, black_scholes, validate_option_chain

__all__ = [
    "analyze_futures_contract",
    "analyze_option_chain",
    "analyze_option_scenarios",
    "black_scholes",
    "classify_open_interest",
    "validate_option_chain",
]
