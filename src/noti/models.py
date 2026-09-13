"""매물 도메인 모델과 가격/층수 파서."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 네이버가 쓰는 거래 유형 코드
TRADE_TYPE_NAMES = {"A1": "매매", "B1": "전세", "B2": "월세", "B3": "단기임대"}


def parse_price(text: str | None) -> tuple[int | None, int | None]:
    """가격 문자열을 (보증금, 월세) 만원 단위 정수로 변환한다.

    >>> parse_price("11억 5,000")
    (115000, None)
    >>> parse_price("5억")
    (50000, None)
    >>> parse_price("1,000/70")
    (1000, 70)
    """
    if not text:
        return None, None

    deposit_text, _, monthly_text = text.partition("/")
    deposit = _parse_amount(deposit_text)
    monthly = _parse_amount(monthly_text) if monthly_text else None
    return deposit, monthly


def _parse_amount(text: str) -> int | None:
    """'11억 5,000' / '9,500' / '3억5천' 형태를 만원 단위 정수로."""
    cleaned = text.replace(",", "").replace(" ", "").strip()
    if not cleaned:
        return None

    match = re.fullmatch(r"(?:(\d+)억)?(?:(\d+)천)?(\d+)?", cleaned)
    if not match or not any(match.groups()):
        return None

    eok, chun, rest = match.groups()
    total = 0
    if eok:
        total += int(eok) * 10_000
    if chun:
        total += int(chun) * 1_000
    if rest:
        total += int(rest)
    return total or None


def parse_floor(floor_info: str | None) -> tuple[int | None, int | None]:
    """'12/25' -> (12, 25). '고/25' 처럼 숫자가 아니면 해당 값은 None."""
    if not floor_info:
        return None, None

    current_text, _, total_text = floor_info.partition("/")
    return _maybe_int(current_text), _maybe_int(total_text)


def _maybe_int(text: str) -> int | None:
    text = text.strip()
    return int(text) if text.lstrip("-").isdigit() else None


@dataclass(slots=True)
class Listing:
    """네이버 부동산 매물 한 건."""

    article_no: str
    name: str
    trade_type: str
    price_text: str
    deposit: int | None = None  # 만원. 매매면 매매가, 전/월세면 보증금
    monthly: int | None = None  # 만원. 월세만
    area_m2: float | None = None  # 전용면적
    supply_area_m2: float | None = None  # 공급면적
    area_name: str | None = None  # "84A" 같은 평형 이름
    floor: int | None = None
    total_floor: int | None = None
    direction: str | None = None
    building_name: str | None = None
    realtor: str | None = None
    confirm_date: str | None = None
    feature_desc: str | None = None
    tags: list[str] = field(default_factory=list)
    complex_no: str | None = None

    @property
    def url(self) -> str:
        return f"https://new.land.naver.com/articles/{self.article_no}"

    @property
    def fingerprint(self) -> str | None:
        """'같은 집' 판별용 지문. 단서가 부족하면 None(=묶지 않음).

        매물번호는 중개업소마다 다르게 발급되므로, 같은 집이 여러 건 올라오면
        단지(또는 건물)·전용면적·층·거래유형이 모두 같은 것을 한 집으로 본다.
        가격은 뺀다 — 중개사마다 호가가 다를 수 있고, 가격 변동 알림과도 겹친다.
        """
        place = self.complex_no or self.building_name
        if not place or self.area_m2 is None or self.floor is None:
            return None
        return f"{place}|{self.area_m2:g}|{self.floor}|{self.trade_type}"

    @property
    def searchable_text(self) -> str:
        """키워드 필터가 훑는 텍스트."""
        parts = [self.name, self.building_name, self.feature_desc, *self.tags]
        return " ".join(p for p in parts if p)

    def summary(self) -> str:
        bits = [f"{self.trade_type} {self.price_text}"]
        if self.area_m2:
            bits.append(f"전용 {self.area_m2:g}㎡({self.area_m2 / 3.3058:.0f}평)")
        if self.floor is not None:
            bits.append(f"{self.floor}/{self.total_floor or '?'}층")
        if self.direction:
            bits.append(self.direction)
        return " · ".join(bits)

    @classmethod
    def from_mobile(cls, raw: dict, *, complex_no: str | None = None) -> Listing:
        """m.land.naver.com 응답 1건을 Listing 으로.

        모바일 API 는 가격을 숫자(만원)로 주므로 문자열 파싱이 필요 없다.
        표기 필드(hanPrc/prcInfo)가 있으면 그걸 그대로 쓰고, 없으면 숫자로 만든다.
        """
        deposit = _maybe_int_value(pick(raw, "prc", "dealPrc", "wrprc"))
        monthly = _maybe_int_value(pick(raw, "rentPrc"))
        price_text = str(pick(raw, "hanPrc", "prcInfo", "dealOrWarrantPrc") or "")
        if not price_text:
            price_text = _format_price(deposit, monthly)
        elif deposit is None:  # 표기만 온 경우 숫자는 파싱해서 채운다
            deposit, monthly = parse_price(price_text)

        floor, total_floor = parse_floor(str(pick(raw, "flrInfo", "floorInfo") or "") or None)
        return cls(
            article_no=str(pick(raw, "atclNo", "articleNo") or ""),
            name=str(pick(raw, "atclNm", "articleName", "bildNm") or "이름 없음"),
            trade_type=str(pick(raw, "tradTpNm", "tradeTypeName") or "?"),
            price_text=price_text,
            deposit=deposit,
            monthly=monthly,
            area_m2=_maybe_float(pick(raw, "spc2", "area2")),
            supply_area_m2=_maybe_float(pick(raw, "spc1", "area1")),
            area_name=_maybe_str(pick(raw, "areaName", "spcNm")),
            floor=floor,
            total_floor=total_floor,
            direction=_maybe_str(pick(raw, "direction")),
            building_name=_maybe_str(pick(raw, "bildNm", "buildingName")),
            realtor=_maybe_str(pick(raw, "rltrNm", "realtorName")),
            confirm_date=_maybe_str(pick(raw, "atclCfmYmd", "cfmYmd", "articleConfirmYmd")),
            feature_desc=_maybe_str(pick(raw, "atclFetrDesc", "articleFeatureDesc")),
            tags=list(raw.get("tagList") or []),
            complex_no=complex_no or _maybe_str(pick(raw, "hscpNo", "complexNo")),
        )

    @classmethod
    def from_api(cls, raw: dict) -> Listing:
        """네이버 articles API 응답 1건을 Listing 으로."""
        price_text = raw.get("dealOrWarrantPrc") or ""
        deposit, monthly = parse_price(price_text)
        # 월세는 rentPrc 에 따로 오는 경우가 있다.
        if monthly is None and raw.get("rentPrc"):
            _, monthly = parse_price(f"0/{raw['rentPrc']}")
        floor, total_floor = parse_floor(raw.get("floorInfo"))

        return cls(
            article_no=str(raw.get("articleNo")),
            name=raw.get("articleName") or raw.get("buildingName") or "이름 없음",
            trade_type=raw.get("tradeTypeName") or TRADE_TYPE_NAMES.get(raw.get("tradeType", ""), "?"),
            price_text=price_text,
            deposit=deposit,
            monthly=monthly,
            area_m2=_maybe_float(raw.get("area2")),
            supply_area_m2=_maybe_float(raw.get("area1")),
            area_name=raw.get("areaName"),
            floor=floor,
            total_floor=total_floor,
            direction=raw.get("direction"),
            building_name=raw.get("buildingName"),
            realtor=raw.get("realtorName"),
            confirm_date=raw.get("articleConfirmYmd"),
            feature_desc=raw.get("articleFeatureDesc"),
            tags=list(raw.get("tagList") or []),
            complex_no=str(raw["complexNo"]) if raw.get("complexNo") else None,
        )


def pick(raw: dict, *keys: str) -> object | None:
    """여러 후보 키 중 먼저 값이 있는 것을 고른다.

    모바일 API 는 응답 필드 이름이 엔드포인트마다 조금씩 다르다(atclNm/atclNo,
    prcInfo/hanPrc 등). 이름 하나가 바뀌어도 전체가 깨지지 않게 후보를 나열해 둔다.
    """
    for key in keys:
        value = raw.get(key)
        if value not in (None, "", []):
            return value
    return None


def _maybe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _maybe_int_value(value: object) -> int | None:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _maybe_str(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _format_price(deposit: int | None, monthly: int | None) -> str:
    """만원 단위 숫자를 '9억 5,000' / '1,000/70' 표기로."""
    if deposit is None:
        return ""
    eok, rest = divmod(deposit, 10_000)
    text = f"{eok}억 {rest:,}" if eok and rest else f"{eok}억" if eok else f"{deposit:,}"
    return f"{text}/{monthly:,}" if monthly else text
