from noti.config import Criteria
from noti.filters import matches
from noti.models import Listing


def make(**kwargs) -> Listing:
    base = {
        "article_no": "1",
        "name": "○○아파트",
        "trade_type": "전세",
        "price_text": "8억",
        "deposit": 80000,
        "area_m2": 84.9,
        "floor": 7,
        "total_floor": 15,
        "direction": "남향",
    }
    base.update(kwargs)
    return Listing(**base)


def test_price_and_area_bounds():
    criteria = Criteria(max_deposit=90000, min_area_m2=84)
    assert matches(make(), criteria)
    assert not matches(make(deposit=95000), criteria)
    assert not matches(make(area_m2=59.9), criteria)


def test_floor_rules():
    assert not matches(make(floor=1), Criteria(exclude_first_floor=True))
    assert not matches(make(floor=2), Criteria(min_floor=3))
    assert matches(make(floor=3), Criteria(min_floor=3))


def test_direction_filter():
    criteria = Criteria(directions=["남향", "남동향"])
    assert matches(make(), criteria)
    assert not matches(make(direction="북향"), criteria)
    # 방향 정보가 없는 매물은 걸러내지 않는다
    assert matches(make(direction=None), criteria)


def test_keywords():
    listing = make(feature_desc="전세 단기 가능, 올수리")
    assert not matches(listing, Criteria(exclude_keywords=["단기"]))
    assert matches(listing, Criteria(include_keywords=["올수리"]))
    assert not matches(listing, Criteria(include_keywords=["복층"]))


def test_monthly_rent():
    listing = make(trade_type="월세", deposit=1000, monthly=130)
    assert not matches(listing, Criteria(max_monthly=120))
    assert matches(listing, Criteria(max_monthly=150, max_deposit=2000))
