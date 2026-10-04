from datetime import datetime
from services.market_calendar import IST, market_status


def test_added_2026_january_15_holiday_is_closed():
    s=market_status(datetime(2026,1,15,10,0,tzinfo=IST))
    assert not s["is_open"] and "Municipal" in s["reason"]


def test_union_budget_sunday_2026_is_live_session():
    s=market_status(datetime(2026,2,1,10,0,tzinfo=IST))
    assert s["is_open"] and s["special_session"]


def test_normal_weekday_session():
    assert market_status(datetime(2026,8,10,10,0,tzinfo=IST))["is_open"]
    assert not market_status(datetime(2026,8,10,18,0,tzinfo=IST))["is_open"]
