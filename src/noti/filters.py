"""조건(Criteria) 대비 매물 매칭."""

from __future__ import annotations

from .config import Criteria
from .models import Listing


def matches(listing: Listing, criteria: Criteria) -> bool:
    """매물이 조건을 모두 만족하면 True. 값이 없는 항목은 통과로 취급한다."""
    checks = (
        _in_range(listing.deposit, criteria.min_deposit, criteria.max_deposit),
        _in_range(listing.monthly, criteria.min_monthly, criteria.max_monthly),
        _in_range(listing.area_m2, criteria.min_area_m2, criteria.max_area_m2),
        _in_range(listing.floor, criteria.min_floor, criteria.max_floor),
        not (criteria.exclude_first_floor and listing.floor == 1),
        _direction_ok(listing, criteria),
        _keywords_ok(listing, criteria),
    )
    return all(checks)


def _in_range(value: float | None, low: float | None, high: float | None) -> bool:
    if value is None:
        # 값을 못 읽은 매물은 조건이 걸려 있으면 보수적으로 제외하지 않고 통과시킨다.
        return True
    if low is not None and value < low:
        return False
    return not (high is not None and value > high)


def _direction_ok(listing: Listing, criteria: Criteria) -> bool:
    if not criteria.directions:
        return True
    if not listing.direction:
        return True
    return listing.direction in criteria.directions


def _keywords_ok(listing: Listing, criteria: Criteria) -> bool:
    text = listing.searchable_text
    if any(word in text for word in criteria.exclude_keywords):
        return False
    if criteria.include_keywords:
        return any(word in text for word in criteria.include_keywords)
    return True
