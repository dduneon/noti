"""환경변수(.env) 설정과 YAML 감시 조건 로딩."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Criteria(BaseModel):
    """매물 하나가 알림 대상인지 판단하는 조건. 모든 항목은 선택."""

    min_deposit: int | None = None  # 만원
    max_deposit: int | None = None
    min_monthly: int | None = None
    max_monthly: int | None = None
    min_area_m2: float | None = None  # 전용면적
    max_area_m2: float | None = None
    min_floor: int | None = None
    max_floor: int | None = None
    exclude_first_floor: bool = False
    directions: list[str] = Field(default_factory=list)  # 예: ["남향", "남동향"]
    include_keywords: list[str] = Field(default_factory=list)  # 하나라도 포함되면 통과
    exclude_keywords: list[str] = Field(default_factory=list)  # 하나라도 포함되면 탈락


class Target(BaseModel):
    """감시 대상 하나(단지 또는 지역)."""

    name: str
    kind: Literal["complex", "region"] = "complex"
    complex_no: str | None = None  # kind=complex 일 때 필수
    cortar_no: str | None = None  # kind=region 일 때 필수 (지역 코드 10자리)
    # 구/시 코드를 주면 하위 동을 자동으로 펼쳐 전부 감시한다(동 코드를 주면 그대로 사용).
    expand_subregions: bool = True
    max_subregions: int = 30  # 펼친 하위 지역 수 상한(요청 폭주 방지)
    trade_types: list[str] = Field(default_factory=lambda: ["A1"])  # A1 매매 B1 전세 B2 월세
    real_estate_types: list[str] = Field(default_factory=lambda: ["APT"])
    max_pages: int = 3
    criteria: Criteria = Field(default_factory=Criteria)

    @model_validator(mode="after")
    def _check_identifier(self) -> Target:
        if self.kind == "complex" and not self.complex_no:
            raise ValueError(f"target '{self.name}': kind=complex 이면 complex_no 가 필요합니다")
        if self.kind == "region" and not self.cortar_no:
            raise ValueError(f"target '{self.name}': kind=region 이면 cortar_no 가 필요합니다")
        return self


class WatchConfig(BaseModel):
    notify_on_first_run: bool = False  # 첫 실행 때 기존 매물 전부 알림 보낼지
    targets: list[Target]

    @classmethod
    def load(cls, path: str | Path) -> WatchConfig:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    def save(self, path: str | Path) -> None:
        """설정 페이지에서 저장할 때 쓰는 원자적 쓰기.

        기존 파일은 .bak 으로 한 벌 남긴다. YAML 주석은 보존되지 않는다.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.with_suffix(target.suffix + ".bak").write_text(
                target.read_text(encoding="utf-8"), encoding="utf-8"
            )

        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(self.to_yaml(), encoding="utf-8")
        tmp.replace(target)

    def to_yaml(self) -> str:
        return yaml.safe_dump(
            self.model_dump(mode="json"), allow_unicode=True, sort_keys=False, indent=2
        )


class Settings(BaseSettings):
    """.env / 환경변수에서 읽는 런타임 설정."""

    model_config = SettingsConfigDict(env_prefix="NOTI_", env_file=".env", extra="ignore")

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    config_path: Path = Path("config.yaml")
    db_path: Path = Path("noti.db")

    poll_interval_seconds: int = 300
    jitter_seconds: int = 30  # 매 주기마다 0~jitter 만큼 랜덤 지연
    request_delay_seconds: float = 1.0  # 페이지/대상 사이 최소 간격
    request_timeout_seconds: float = 10.0
    max_notifications_per_cycle: int = 20  # 폭주 방지

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)
