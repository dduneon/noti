# noti — 네이버 부동산 조건 매물 알림 봇

네이버 부동산을 주기적으로 조회해서 **내 조건에 맞는 새 매물이 뜨면 텔레그램으로 알려주는** 파이썬 서비스입니다.
별도 모바일 앱 없이, 텔레그램 앱의 푸시 알림을 그대로 쓰는 구조입니다.

```
[폴링 루프] → [네이버 부동산 API] → [조건 필터] → [SQLite 중복 제거] → [텔레그램 전송]
```

## 빠른 시작

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env            # 텔레그램 토큰/챗ID 입력
cp config.example.yaml config.yaml   # 감시 조건 작성

noti test-notify                # 알림 채널 확인
noti once --console             # 조회/필터가 잘 되는지 1회 실행 (콘솔 출력)
noti run                        # 상시 감시
```

### 텔레그램 준비

1. 텔레그램에서 **@BotFather** 에게 `/newbot` → 봇 토큰 발급 → `NOTI_TELEGRAM_BOT_TOKEN`
2. 만든 봇과 대화를 한 번 시작(`/start`)하고, **@userinfobot** 으로 내 chat id 확인 → `NOTI_TELEGRAM_CHAT_ID`
   (그룹으로 받을 땐 봇을 그룹에 넣고 그룹 chat id(음수)를 사용)

## 감시 조건 작성 (config.yaml)

```yaml
notify_on_first_run: false   # 첫 실행에 기존 매물 전부 알릴지 (보통 false)

targets:
  - name: "○○아파트 전세 9억 이하"
    kind: complex            # complex(단지) | region(지역)
    complex_no: "111515"     # new.land.naver.com/complexes/111515 의 숫자
    trade_types: ["B1"]      # A1 매매 / B1 전세 / B2 월세
    real_estate_types: ["APT"]   # APT, OPST(오피스텔), VL(빌라), ABYG(분양권) 등
    criteria:
      max_deposit: 90000     # 만원 단위 (= 9억)
      min_area_m2: 84        # 전용면적
      min_floor: 3
      exclude_first_floor: true
      directions: ["남향", "남동향"]
      exclude_keywords: ["단기"]
```

- 조건은 **모두 선택 항목**이고, 적은 항목만 AND 로 평가됩니다.
- 매물에서 값을 못 읽은 항목(예: 방향 정보 없음)은 걸러내지 않고 통과시킵니다 — 놓치는 것보다 낫기 때문.
- 지역(`kind: region`) 감시에 쓸 법정동 코드는 `noti regions 0000000000` 부터 시/도 → 시군구 → 동 순으로 타고 내려가며 찾습니다.

## 동작 방식

- **중복 방지**: 알린 매물은 SQLite(`noti.db`)에 `(대상, 매물번호)` 로 기록합니다. 같은 매물은 다시 알리지 않고, **가격이 바뀐 매물은 다시 알립니다.**
- **첫 실행 폭탄 방지**: 처음 실행할 땐 기존 매물을 기록만 하고 알림은 보내지 않습니다(`notify_on_first_run: true` 로 변경 가능). 이후부터 진짜 신규만 옵니다.
- **호출 예의**: 기본 주기 5분 + 0~30초 랜덤 지터, 페이지/대상 사이 1초 간격. 한 사이클 최대 20건까지만 전송합니다. 주기를 무리하게 줄이지 마세요.
- **실패 내성**: 한 대상의 조회가 실패해도 다른 대상은 계속 처리하고, 다음 주기에 재시도합니다.

## 상시 실행

```bash
docker compose up -d --build     # .env + config.yaml 을 마운트
docker compose logs -f
```

systemd 를 쓴다면 `ExecStart=/opt/noti/.venv/bin/noti run` 에 `Restart=always` 정도면 충분합니다.

## 알아둘 점 (중요)

- 네이버 부동산에는 **공개된 공식 API가 없습니다.** 이 프로젝트는 웹에서 쓰는 내부 엔드포인트
  (`/api/articles/complex/{complexNo}`, `/api/articles`)를 호출하며, `new.land.naver.com` 접속 시 내려오는
  `REALESTATE` 쿠키(JWT)를 `Authorization: Bearer` 로 사용합니다.
  **네이버가 스키마나 인증 방식을 바꾸면 동작이 깨질 수 있고**, 그때는 `src/noti/sources/naver.py` 만 고치면 됩니다
  (도메인 모델·필터·알림은 그대로).
- 자동 수집은 사이트 이용약관 이슈가 있을 수 있습니다. 개인이 본인 조건의 매물을 확인하는 저빈도 사용을 전제로 하고,
  요청 간격을 줄이거나 대량으로 수집하는 용도로 바꾸지 마세요. 차단당하면 그건 신호입니다.
- 이 저장소의 네트워크 환경 제약으로 **실 API 응답으로는 검증하지 못했습니다.** 로컬에서 `noti once --console` 로
  첫 조회를 돌려보고, 필드가 비어 보이면 `--log-level DEBUG` 로 원본 응답 키를 확인해 `Listing.from_api` 를 맞춰주세요.

## 개발

```bash
pytest        # 파서 / 필터 / 중복제거 / 서비스 루프 단위 테스트
ruff check src tests
```

| 모듈 | 역할 |
| --- | --- |
| `sources/naver.py` | 네이버 API 호출, 토큰 발급/갱신, 페이지네이션 |
| `models.py` | `Listing` 모델, 가격("11억 5,000")·층수 파서 |
| `filters.py` | `Criteria` 대비 매칭 |
| `store.py` | SQLite 중복 제거, 첫 실행 여부 |
| `notifiers.py` | 텔레그램 / 콘솔 전송 |
| `service.py` | 폴링 루프 |
