from services.sentiment import score_headline


def test_positive_negative_and_neutral_headlines():
    assert score_headline("Company beats estimates as profits surge")["label"] == "Positive"
    assert score_headline("Shares plunge after profit warning")["label"] == "Negative"
    assert score_headline("Company schedules annual shareholder meeting")["label"] == "Neutral"


def test_negation_reverses_simple_lexicon_signal():
    result = score_headline("Results are not weak after the update")
    assert result["label"] == "Positive"
    assert result["score"] > 0


def test_empty_headline_is_neutral():
    assert score_headline("") == {"label": "Neutral", "score": 0.0, "icon": "⚪"}
