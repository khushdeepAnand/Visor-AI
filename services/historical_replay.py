"""Real historical NIFTY 50 replay scenarios for the weekly-challenge feature.

Each scenario is a real, sourced NSE Nifty 50 session pair (an "entry" close and
a hidden "outcome" close on the next reported session). The figures below are
taken from contemporaneous market reporting, not simulated or fabricated:

- NIFTY_COVID_CRASH_2020: Nifty fell 1,135 points (-12.88%) to close at 7,610.25
  on 23 Mar 2020 as India entered COVID lockdown (Business Standard, 23 Mar
  2020). It closed at 8,317.85, +6.62% on the session, on 25 Mar 2020 as the
  first relief rally began (Business Standard, 25 Mar 2020 market wrap).
- NIFTY_BUDGET_RALLY_2021: Nifty jumped 646.60 points to close at 14,281.20 on
  1 Feb 2021, its best Union Budget day performance in 20 years (ClearTax,
  "Budget Day Market Movement History in India"). The rally extended the next
  session, 2 Feb 2021, closing up 367.80 points (+2.57%) at 14,648.00
  (Business Standard, "Market extends Budget rally").
- NIFTY_UKRAINE_SHOCK_2022: Nifty closed at 17,063.25 on 23 Feb 2022. The next
  session, 24 Feb 2022, it slumped 815.30 points (-4.78%) to 16,247.95 as
  Russia invaded Ukraine (Business Standard market wraps for both sessions).

This module never fetches live data and never claims these are exhaustive
records of the sessions in question -- only the closing levels cited above are
used, and every scenario is fully deterministic given (scenario, choice).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.db.factory import dao_session
from services.paper_trading_v6 import _fill_price, ensure_schema  # noqa: F401 (schema import keeps table creation centralised)

DEFAULT_SPREAD_BPS = 5.0
DEFAULT_SLIPPAGE_BPS = 2.0
HEDGE_FLOOR_RETURN = -0.02  # simplified educational collar: caps simulated downside at -2%

SCENARIOS: list[dict[str, Any]] = [
    {
        "key": "NIFTY_COVID_CRASH_2020",
        "title": "COVID lockdown crash",
        "narrative": (
            "23 Mar 2020: Nifty 50 closes at 7,610.25, down 12.88% as India enters "
            "COVID-19 lockdown. Circuit-breaker selling across every sector."
        ),
        "index": "NIFTY 50",
        "entry_date": "2020-03-23",
        "entry_close": 7610.25,
        "prev_close": 8745.45,
        "outcome_date": "2020-03-25",
        "outcome_close": 8317.85,
        "choices": ["HOLD", "BUY_DIP", "EXIT"],
        "source": "Business Standard market wraps, 23 Mar 2020 and 25 Mar 2020",
    },
    {
        "key": "NIFTY_BUDGET_RALLY_2021",
        "title": "Union Budget 2021 rally",
        "narrative": (
            "1 Feb 2021: Nifty 50 closes at 14,281.20, up 646.60 points -- the best "
            "Union Budget day session in 20 years -- after expansionary Budget "
            "announcements."
        ),
        "index": "NIFTY 50",
        "entry_date": "2021-02-01",
        "entry_close": 14281.20,
        "prev_close": 13634.60,
        "outcome_date": "2021-02-02",
        "outcome_close": 14648.00,
        "choices": ["HOLD", "BUY_DIP", "EXIT"],
        "source": "ClearTax Budget Day history; Business Standard, 2 Feb 2021",
    },
    {
        "key": "NIFTY_UKRAINE_SHOCK_2022",
        "title": "Russia-Ukraine invasion shock",
        "narrative": (
            "23 Feb 2022: Nifty 50 closes at 17,063.25 after six straight losing "
            "sessions amid rising Russia-Ukraine tension, but off the day's lows."
        ),
        "index": "NIFTY 50",
        "entry_date": "2022-02-23",
        "entry_close": 17063.25,
        "prev_close": 17092.20,
        "outcome_date": "2022-02-24",
        "outcome_close": 16247.95,
        "choices": ["HOLD", "BUY_DIP", "EXIT", "HEDGE"],
        "source": "Business Standard market wraps, 23 Feb 2022 and 24 Feb 2022",
    },
]

_BY_KEY = {item["key"]: item for item in SCENARIOS}


class ScenarioNotFoundError(KeyError):
    pass


class InvalidChoiceError(ValueError):
    pass


def list_scenarios() -> list[dict[str, Any]]:
    """Entry-only view: never leaks the outcome close before a choice is made."""
    return [
        {
            "key": item["key"],
            "title": item["title"],
            "narrative": item["narrative"],
            "index": item["index"],
            "entry_date": item["entry_date"],
            "entry_close": item["entry_close"],
            "prev_close": item["prev_close"],
            "choices": item["choices"],
            "source": item["source"],
        }
        for item in SCENARIOS
    ]


def _scenario(key: str) -> dict[str, Any]:
    item = _BY_KEY.get(key)
    if item is None:
        raise ScenarioNotFoundError(key)
    return item


def _simulate_choice(
    scenario: dict[str, Any],
    choice: str,
    spread_bps: float,
    slippage_bps: float,
) -> dict[str, Any]:
    entry_close = float(scenario["entry_close"])
    outcome_close = float(scenario["outcome_close"])
    raw_return = outcome_close / entry_close - 1.0

    if choice == "HOLD":
        entry_fill = entry_close
        exit_fill = outcome_close
    elif choice == "BUY_DIP":
        entry_fill = _fill_price("BUY", {"price": entry_close}, spread_bps, slippage_bps)
        exit_fill = outcome_close
    elif choice == "EXIT":
        # Locks in cash at the entry session; no further exposure to the reveal.
        entry_fill = _fill_price("SELL", {"price": entry_close}, spread_bps, slippage_bps)
        exit_fill = entry_fill
    elif choice == "HEDGE":
        if "HEDGE" not in scenario["choices"]:
            raise InvalidChoiceError(choice)
        # Simplified educational collar: caps downside, keeps upside uncapped.
        # This is a teaching simplification, not a real options price -- it is
        # never presented as an exact hedge P&L.
        hedged_return = max(raw_return, HEDGE_FLOOR_RETURN)
        entry_fill = entry_close
        exit_fill = entry_close * (1.0 + hedged_return)
    else:
        raise InvalidChoiceError(choice)

    return_pct = (exit_fill / entry_fill - 1.0) if entry_fill else 0.0
    return {
        "choice": choice,
        "entry_fill": round(entry_fill, 2),
        "exit_fill": round(exit_fill, 2),
        "return_pct": round(return_pct * 100.0, 4),
    }


def resolve_choice(
    key: str,
    choice: str,
    spread_bps: float = DEFAULT_SPREAD_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
) -> dict[str, Any]:
    """Reveal a scenario's real outcome and the simulated result of one choice.

    Also returns every other available choice's simulated result so the
    comparison is genuinely educational once the outcome is known.
    """
    scenario = _scenario(key)
    if choice not in scenario["choices"]:
        raise InvalidChoiceError(choice)

    chosen = _simulate_choice(scenario, choice, spread_bps, slippage_bps)
    all_choices = {
        option: _simulate_choice(scenario, option, spread_bps, slippage_bps)["return_pct"]
        for option in scenario["choices"]
    }

    return {
        "key": scenario["key"],
        "title": scenario["title"],
        "index": scenario["index"],
        "entry_date": scenario["entry_date"],
        "entry_close": scenario["entry_close"],
        "outcome_date": scenario["outcome_date"],
        "outcome_close": scenario["outcome_close"],
        "source": scenario["source"],
        "choice": chosen,
        "all_choice_returns_pct": all_choices,
    }


def record_attempt(user_id: int, key: str, choice: str) -> dict[str, Any]:
    """Persist (or replace) one user's attempt at a scenario and return the reveal."""
    result = resolve_choice(key, choice)
    ensure_schema()
    with dao_session() as factory:
        conn = factory.db
        conn.execute(conn.sql(
        """
        INSERT INTO paper_challenge_entries(user_id, challenge_key, choice, score, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, challenge_key) DO UPDATE SET
            choice=excluded.choice, score=excluded.score, created_at=excluded.created_at
        """),
        (int(user_id), key, choice, result["choice"]["return_pct"], datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    return result


def my_attempts(user_id: int) -> list[dict[str, Any]]:
    with dao_session() as factory:
        rows = factory.db.fetchall(factory.db.sql("SELECT challenge_key,choice,score,created_at FROM paper_challenge_entries WHERE user_id=?"), (int(user_id),))
    return [
        {"key": row["challenge_key"], "choice": row["choice"], "return_pct": row["score"], "attempted_at": row["created_at"]}
        for row in rows
    ]
