# noti — 네이버 부동산 조건 매물 알림 봇

네이버 부동산을 주기적으로 조회해서 **내 조건에 맞는 새 매물이 뜨면 텔레그램으로 알려주는** 파이썬 서비스입니다.
별도 모바일 앱 없이, 텔레그램 앱의 푸시 알림을 그대로 쓰는 구조입니다.

```
[폴링 루프] → [네이버 부동산 API] → [조건 필터] → [SQLite 중복 제거] → [텔레그램 전송]
```

## 빠른 시작

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[web]"        # 설정 페이지 없이 CLI 만 쓸 거면 pip install -e .

cp .env.example .env            # 텔레그램 토큰/챗ID 입력
cp config.example.yaml config.yaml   # 감시 조건 작성

noti web                        # 설정 페이지 → http://127.0.0.1:8765
noti test-notify                # 알림 채널 확인
noti once --console             # 조회/필터가 잘 되는지 1회 실행 (콘솔 출력)
noti run                        # 상시 감시
```

### 네이버 접속 방식

기본값은 **모바일 웹 엔드포인트(`m.land.naver.com`)** 이고, **토큰이 필요 없습니다.** 설정할 것도 없습니다.

데스크톱(`new.land.naver.com`)의 매물 API 는 페이지의 JS 가 브라우저에서 만들어 붙이는
`Authorization: Bearer <JWT>` 를 요구합니다(로그인 자격증명이 아니라 방문자마다 발급되는 익명
토큰입니다). 서버가 접속만 해서는 얻을 수 없고 주기적으로 만료되므로 기본으로 쓰지 않습니다.
굳이 쓰려면 `NOTI_SOURCE=desktop` 과 함께 브라우저에서 복사한 토큰을
`NOTI_NAVER_AUTH_TOKEN` 에 넣으면 되고, 만료되면 텔레그램으로 갱신 안내가 갑니다.

### 텔레그램 준비

1. 텔레그램에서 **@BotFather** 에게 `/newbot` → 봇 토큰 발급 → `NOTI_TELEGRAM_BOT_TOKEN`
2. 만든 봇과 대화를 한 번 시작(`/start`)하고, **@userinfobot** 으로 내 chat id 확인 → `NOTI_TELEGRAM_CHAT_ID`
   (그룹으로 받을 땐 봇을 그룹에 넣고 그룹 chat id(음수)를 사용)

## 설정 페이지 (권장)

```bash
noti web            # http://127.0.0.1:8765
```

브라우저에서 감시 대상을 만들고 조건을 채웁니다. YAML 을 직접 쓰지 않아도 됩니다.

- **지역 찾기**: `서울 강남구 역삼동` 처럼 입력 → 후보를 클릭하면 `cortarNo` 가 자동으로 채워집니다.
- **거래 유형 / 매물 종류 / 방향**은 칩으로 선택, 금액은 만원 단위로 넣으면 `10억` 처럼 환산해 보여줍니다.
- **미리보기**: 저장 전에 "이 조건으로 미리보기" 를 누르면 지금 조건에 몇 건이 걸리는지 실제로 조회해 보여줍니다(알림은 안 나감).
- **알림 테스트** 버튼으로 텔레그램 연결을 확인합니다.
- 저장하면 `config.yaml` 에 쓰이고(이전 파일은 `.bak` 로 백업), **실행 중인 `noti run` 이 다음 사이클에 자동으로 새 설정을 반영**합니다. 재시작 불필요.

> ⚠️ 설정 페이지에는 로그인이 없습니다. 기본값인 `127.0.0.1` 바인딩을 유지하고 외부에 열지 마세요.
> 원격에서 쓰려면 SSH 터널(`ssh -L 8765:127.0.0.1:8765 ...`)을 권합니다.
> 저장 시 YAML 주석은 보존되지 않습니다.

## 감시 조건 작성 (config.yaml)

설정 페이지 대신 파일을 직접 편집해도 됩니다.

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
### 지역 단위로 감시하기

단지(`kind: complex`)뿐 아니라 **구/동 단위로도 감시**할 수 있습니다.

```bash
noti find-region 서울 강남구        # 1168000000  서울시 강남구
noti find-region 서울 강남구 역삼동  # 1168010100  서울시 강남구 역삼동
noti regions 1168000000            # 강남구 하위 동 전체 나열
```

```yaml
  - name: "강남구 전세 10억 이하"
    kind: region
    cortar_no: "1168000000"   # 구 코드
    expand_subregions: true   # 하위 동 전체로 자동 확장 (기본값)
    max_subregions: 30
    trade_types: ["B1"]
    real_estate_types: ["APT", "OPST"]
    max_pages: 2
    criteria:
      max_deposit: 100000
      min_area_m2: 59
```

네이버 매물 목록 API 는 **동 단위 코드**를 기대하기 때문에, 구/시 코드를 주면 하위 동 목록을 받아
동마다 조회합니다(지역 목록은 캐시). 동 코드를 직접 주면 그대로 씁니다.

⚠️ 요청량 주의: 구 하나가 동 20개면 `max_pages: 2` 기준 한 사이클에 40회 호출이 됩니다.
넓은 지역을 볼수록 `max_pages` 를 줄이고 `NOTI_POLL_INTERVAL_SECONDS` 를 늘리세요
(구 단위면 10~15분 권장). 조건을 좁게 잡는 것이 알림 품질에도, 사이트에도 낫습니다.

## 동작 방식

- **중복 방지**: 알린 매물은 SQLite(`noti.db`)에 기록하고 다시 알리지 않습니다. 판단 기준은 두 가지입니다.
  - **매물번호**: 같은 매물의 재등장을 막습니다.
  - **지문(단지·전용면적·층·거래유형)**: 매물번호는 중개업소마다 다르게 발급되므로,
    같은 집을 여러 중개사가 올린 경우를 한 건으로 묶습니다(`merge_same_property`).
    층이나 면적을 못 읽은 매물은 오인할 수 있어 묶지 않습니다.

  | 설정 | 기본값 | 뜻 |
  | --- | --- | --- |
  | `dedupe_scope` | `global` | `global`: "강남구 전세"와 "역삼동 전세"를 둘 다 감시해도 같은 매물은 한 번만. `target`: 대상별로 각각 알림 |
  | `merge_same_property` | `true` | 같은 집을 올린 다른 중개사 매물을 묶음 |
  | `notify_on_price_change` | `true` | 이미 알린 매물의 가격이 바뀌면 다시 알림. 끄면 매물당 딱 한 번만 |

  구버전 DB 는 처음 열 때 새 형식으로 자동 변환되며, 이미 알린 매물이 다시 오지 않습니다.
- **첫 실행 폭탄 방지**: 처음 실행할 땐 기존 매물을 기록만 하고 알림은 보내지 않습니다(`notify_on_first_run: true` 로 변경 가능). 이후부터 진짜 신규만 옵니다.
- **호출 예의**: 기본 주기 5분 + 0~30초 랜덤 지터, 페이지/대상 사이 1초 간격. 한 사이클 최대 20건까지만 전송합니다. 주기를 무리하게 줄이지 마세요.
- **실패 내성**: 한 대상의 조회가 실패해도 다른 대상은 계속 처리하고, 다음 주기에 재시도합니다.

## 서버에 올리기

24시간 돌아야 알림이 의미가 있으니, 노트북 말고 항상 켜져 있는 곳에 올리세요.
CPU·메모리를 거의 쓰지 않아 **라즈베리파이나 가장 싼 VPS로 충분**합니다.

### 방법 A. systemd (우분투/데비안 서버, 라즈베리파이)

```bash
sudo ./deploy/install.sh          # /opt/noti 에 설치 + 유닛 등록
sudo -u noti nano /opt/noti/.env  # 텔레그램 토큰/챗ID
sudo systemctl enable --now noti noti-web
sudo journalctl -u noti -f        # 로그 확인
```

- `noti.service` 는 감시 루프, `noti-web.service` 는 설정 페이지입니다(`deploy/` 참고).
- 설정 페이지는 **127.0.0.1 에만** 바인딩됩니다. 접속은 SSH 터널로:
  ```bash
  ssh -L 8765:127.0.0.1:8765 <서버>   # 이후 브라우저에서 http://127.0.0.1:8765
  ```
- 코드를 업데이트했으면: `sudo ./deploy/install.sh && sudo systemctl restart noti noti-web`
  (조건만 바꿨다면 재시작 불필요 — 루프가 알아서 다시 읽습니다.)

### 방법 B. Docker (compose)

```bash
git clone https://github.com/dduneon/noti.git && cd noti

cp .env.example .env                          # 텔레그램 토큰/챗ID 입력
                                              # NOTI_CONFIG_PATH/NOTI_DB_PATH 는 건드리지 말 것
mkdir -p config
cp config.example.yaml config/config.yaml     # 조건은 나중에 설정 페이지에서 고쳐도 됨
chmod 600 .env

docker compose up -d --build
docker compose logs -f noti
```

컨테이너 두 개가 뜹니다. 둘 다 같은 이미지이고 실행 커맨드만 다릅니다.

| 컨테이너 | 하는 일 | 포트 |
| --- | --- | --- |
| `noti` | 감시 루프 (`noti run`) | 없음(아웃바운드만) |
| `noti-web` | 설정 페이지 (`noti web`) | `127.0.0.1:8765` |

상태는 두 군데에 있습니다.

- `./config` (호스트 디렉터리 마운트) → `config.yaml`. 설정 페이지에서 저장하면 이 파일이 바뀌고,
  감시 루프가 다음 사이클에 자동으로 다시 읽습니다. **재시작 불필요.**
- `noti-data` 볼륨 → `/data/noti.db`. "이미 알린 매물" 기록이라 컨테이너를 새로 만들어도 유지됩니다.

> 설정 파일은 **디렉터리째** 마운트합니다(`./config:/config`). 단일 파일을 bind mount 하면
> 그 경로가 마운트 지점이 되어 설정 페이지의 원자적 저장(rename)이 실패합니다.

> `.env` 의 `NOTI_CONFIG_PATH` / `NOTI_DB_PATH` 는 비워 두세요. `env_file` 은 이미지의 ENV 를
> 덮어쓰기 때문에, 여기에 `config.yaml` 같은 상대경로가 들어 있으면 컨테이너가 마운트한
> `/config` 대신 엉뚱한 경로를 봅니다(`FileNotFoundError: 'config.yaml'`).
> compose 가 두 경로를 다시 못박아 두긴 했지만, 낡은 `.env` 를 쓰고 있다면 지우는 편이 낫습니다.

설정 페이지 접속은 SSH 터널로:

```bash
ssh -L 8765:127.0.0.1:8765 <서버>     # 이후 브라우저에서 http://127.0.0.1:8765
```

자주 쓰는 명령:

```bash
docker compose logs -f noti          # 감시 로그
docker compose exec noti noti once --console   # 조회가 되는지 즉시 확인(알림 안 감)
docker compose pull && docker compose up -d --build   # 코드 업데이트 후 재배포
docker compose down                  # 정지 (볼륨은 유지)
```

### 방법 C. cron 으로 주기 실행 (상주 프로세스가 싫다면)

```cron
*/10 * * * * cd /opt/noti && set -a && . ./.env && set +a && .venv/bin/noti once >> /var/log/noti.log 2>&1
```

`once` 는 한 사이클만 돌고 끝납니다. 중복 판단은 `noti.db` 파일이 하므로
**DB 경로가 매번 같은 곳을 가리키게** 두세요(컨테이너면 볼륨 필수).

### 어디에 둘까

| 선택지 | 비고 |
| --- | --- |
| 라즈베리파이 / 집 NAS | 전기값만 듦. 국내 가정용 IP라 가장 무난 |
| 국내 VPS (네이버클라우드, 카페24 등) | 월 몇천 원대로 충분 |
| 해외 VPS·클라우드 무료 티어 | 되긴 하지만 해외 IP는 차단·캡차 가능성이 상대적으로 높습니다. 쓰려면 먼저 `noti once --console` 로 조회가 되는지 확인하세요 |

### 운영 체크리스트

- `.env` 에 봇 토큰이 들어갑니다. `chmod 600`, 저장소에 커밋 금지(`.gitignore` 에 이미 있음).
- 설정 페이지는 **인증이 없습니다.** 공인 IP·0.0.0.0 바인딩 금지, 리버스 프록시로 열 거면 최소한 basic auth 를 두세요.
- 알림이 안 오면 **먼저 `noti doctor`** 를 실행하세요 — 토큰 발급, 인증 헤더 표기,
  지역 목록(인증 불필요)과 매물 목록(인증 필요) 조회를 각각 확인합니다.
  매물 목록만 401 이면 인증 헤더 문제이고, 둘 다 실패하면 접속·차단 문제입니다.(도커: `docker compose exec noti noti doctor`).
  토큰 발급을 어느 경로에서 시도했고 어떤 쿠키를 받았는지, 샘플 API 호출이 되는지까지 보여줍니다.
  네이버가 인증·라우팅을 바꾸면 이 출력이 바로 단서가 됩니다.
- 폴링 주기를 무리하게 줄이지 마세요. 넓은 지역을 볼수록 주기를 늘리는 게 맞습니다.

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
| `sources/naver.py` | 네이버 API 호출, 토큰 발급/갱신, 지역 확장·검색, 페이지네이션 |
| `models.py` | `Listing` 모델, 가격("11억 5,000")·층수 파서 |
| `filters.py` | `Criteria` 대비 매칭 |
| `store.py` | SQLite 중복 제거, 첫 실행 여부 |
| `notifiers.py` | 텔레그램 / 콘솔 전송 |
| `service.py` | 폴링 루프, 설정 파일 변경 감지 |
| `web.py` + `static/` | 설정 페이지(FastAPI + 바닐라 JS) |
