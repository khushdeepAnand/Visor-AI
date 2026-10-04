"""The builder swallows group-level join unless the API passes it through."""

import pytest

from api.main import StrategyDefinitionPayload, StrategyGroupPayload, _strategy_body


@pytest.mark.parametrize(
    "join",
    ["and", "or", "AND", "Or"],
)
def test_strategy_body_preserves_group_join(join: str) -> None:
    payload = StrategyDefinitionPayload(
        name="join-check",
        symbols=["RELIANCE"],
        entry=[
            StrategyGroupPayload(
                join=join,
                conditions=[
                    {"metric": "sma_20", "operator": "above", "value": 100.0},
                ],
            )
        ],
        quantity=1.0,
    )
    body = _strategy_body(payload)
    assert body["entry"][0]["join"] == join.lower()


def test_strategy_body_defaults_group_join_to_and() -> None:
    payload = StrategyDefinitionPayload(
        name="default-join",
        symbols=["RELIANCE"],
        entry=[StrategyGroupPayload(conditions=[{"metric": "sma_20", "operator": "above", "value": 100.0}])],
        quantity=1.0,
    )
    body = _strategy_body(payload)
    assert body["entry"][0]["join"] == "and"


def test_strategy_body_preserves_compare_metric() -> None:
    payload = StrategyDefinitionPayload(
        name="golden-cross",
        symbols=["RELIANCE"],
        entry=[
            StrategyGroupPayload(
                conditions=[
                    {
                        "metric": "sma_20",
                        "operator": "cross_above",
                        "compare_metric": "sma_50",
                    }
                ]
            )
        ],
    )

    body = _strategy_body(payload)

    assert body["entry"][0]["conditions"][0]["compare_metric"] == "sma_50"
