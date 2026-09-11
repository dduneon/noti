from noti.models import Listing, parse_floor, parse_price


def test_parse_price_eok_and_man():
    assert parse_price("11억 5,000") == (115000, None)
    assert parse_price("5억") == (50000, None)
    assert parse_price("9,500") == (9500, None)
    assert parse_price("3억5천") == (35000, None)


def test_parse_price_monthly():
    assert parse_price("1,000/70") == (1000, 70)
    assert parse_price("1억/50") == (10000, 50)


def test_parse_price_empty():
    assert parse_price(None) == (None, None)
    assert parse_price("") == (None, None)
    assert parse_price("협의") == (None, None)


def test_parse_floor():
    assert parse_floor("12/25") == (12, 25)
    assert parse_floor("고/25") == (None, 25)
    assert parse_floor(None) == (None, None)


def test_listing_from_api():
    listing = Listing.from_api(
        {
            "articleNo": "2400000001",
            "articleName": "○○아파트",
            "tradeTypeName": "전세",
            "dealOrWarrantPrc": "8억 5,000",
            "area1": 112.0,
            "area2": 84.97,
            "floorInfo": "7/15",
            "direction": "남향",
            "tagList": ["역세권", "올수리"],
            "complexNo": 111515,
        }
    )
    assert listing.deposit == 85000
    assert listing.monthly is None
    assert listing.area_m2 == 84.97
    assert listing.floor == 7 and listing.total_floor == 15
    assert listing.url.endswith("/articles/2400000001")
    assert "역세권" in listing.searchable_text
