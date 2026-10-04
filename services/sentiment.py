"""Financial headline sentiment with an optional FinBERT backend.

The default lexicon scorer is deterministic and dependency-free. Set
STOCKPILOT_SENTIMENT_BACKEND=finbert and install requirements-research.txt to
activate ProsusAI/finbert. Both modes remain contextual cues, not trade signals.
"""

from __future__ import annotations

from functools import lru_cache
import importlib.util
import os
import re
from typing import Any

TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z'-]*")

POSITIVE_WORDS = {
    "advance", "advances", "beat", "beats", "benefit", "boom", "boost",
    "bullish", "buy", "confidence", "expands", "gain", "gains", "growth",
    "high", "higher", "improve", "improves", "jump", "jumps", "outperform",
    "profit", "profits", "rally", "record", "rebound", "rise", "rises",
    "strong", "success", "surge", "surges", "upgrade", "upside", "wins",
}

NEGATIVE_WORDS = {
    "bearish", "crash", "cut", "cuts", "decline", "declines", "downgrade",
    "drop", "drops", "fall", "falls", "fraud", "investigation", "loss",
    "losses", "miss", "misses", "plunge", "recall", "risk", "risks",
    "sell", "slump", "weak", "warning", "warnings", "worse", "downside",
}

NEGATIONS = {"not", "no", "never", "without", "hardly"}
INTENSIFIERS = {"very", "strongly", "sharply", "significantly", "massive"}


def finbert_available() -> bool:
    return importlib.util.find_spec("transformers") is not None and importlib.util.find_spec("torch") is not None


@lru_cache(maxsize=1)
def _finbert_pipeline():
    if not finbert_available():
        raise RuntimeError("FinBERT requires transformers and torch from requirements-research.txt.")
    from transformers import pipeline
    return pipeline(
        "text-classification",
        model=os.getenv("STOCKPILOT_FINBERT_MODEL", "ProsusAI/finbert"),
        tokenizer=os.getenv("STOCKPILOT_FINBERT_MODEL", "ProsusAI/finbert"),
        truncation=True,
    )


def _score_lexicon(headline: Any) -> dict[str, Any]:
    tokens = [token.lower() for token in TOKEN_PATTERN.findall(str(headline or ""))]
    if not tokens:
        return {"label": "Neutral", "score": 0.0, "icon": "⚪"}

    raw_score = 0.0
    for index, token in enumerate(tokens):
        value = 1.0 if token in POSITIVE_WORDS else -1.0 if token in NEGATIVE_WORDS else 0.0
        if value == 0.0:
            continue
        previous = tokens[max(0, index - 2):index]
        if any(word in NEGATIONS for word in previous):
            value *= -1
        if index > 0 and tokens[index - 1] in INTENSIFIERS:
            value *= 1.5
        raw_score += value

    normalized = raw_score / max(1.0, len(tokens) ** 0.5)
    normalized = max(-1.0, min(1.0, normalized))
    if normalized >= 0.12:
        label, icon = "Positive", "🟢"
    elif normalized <= -0.12:
        label, icon = "Negative", "🔴"
    else:
        label, icon = "Neutral", "⚪"
    return {"label": label, "score": round(normalized, 3), "icon": icon, "backend": "lexicon"}


def _score_finbert(headline: Any) -> dict[str, Any]:
    text = str(headline or "").strip()
    if not text:
        return {"label": "Neutral", "score": 0.0, "icon": "⚪", "backend": "finbert"}
    output = _finbert_pipeline()(text[:1500])[0]
    raw_label = str(output.get("label", "neutral")).strip().lower()
    confidence = float(output.get("score", 0.0))
    mapping = {
        "positive": ("Positive", 1.0, "🟢"),
        "negative": ("Negative", -1.0, "🔴"),
        "neutral": ("Neutral", 0.0, "⚪"),
    }
    label, sign, icon = mapping.get(raw_label, ("Neutral", 0.0, "⚪"))
    return {
        "label": label,
        "score": round(sign * confidence, 4),
        "confidence": round(confidence, 4),
        "icon": icon,
        "backend": "finbert",
    }


def score_headline(headline: Any, *, backend: str | None = None) -> dict[str, Any]:
    """Return sentiment using FinBERT when explicitly enabled, otherwise lexicon fallback."""

    selected = str(backend or os.getenv("STOCKPILOT_SENTIMENT_BACKEND", "lexicon")).strip().lower()
    if selected == "finbert":
        try:
            return _score_finbert(headline)
        except Exception as error:
            fallback = _score_lexicon(headline)
            fallback["fallback_reason"] = str(error)
            return fallback
    return _score_lexicon(headline)


def score_headlines(headlines: list[Any], *, backend: str | None = None) -> dict[str, Any]:
    scores = [score_headline(item, backend=backend) for item in headlines]
    average = sum(float(item.get("score", 0.0)) for item in scores) / len(scores) if scores else 0.0
    label = "Positive" if average >= 0.12 else ("Negative" if average <= -0.12 else "Neutral")
    return {
        "label": label,
        "average_score": round(average, 4),
        "headline_count": len(scores),
        "items": scores,
    }
