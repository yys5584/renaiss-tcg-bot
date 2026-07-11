# Renaiss Bot

TGPOKE와 분리한 Renaiss Collector 봇입니다. P0는 공개 그룹에서 `c`로 블라인드
스폰에 참여하고, 숨겨진 가격을 추측한 뒤 검증 가능한 Renaiss 기준값을 공개하며
대화가 이어지는 흐름입니다. Renaiss와 연계한 커뮤니티 콜라보 게임이며 공식
Renaiss 웹사이트나 제품이 아닙니다. 모든 카드는 게임 안 수집품이고 실물 카드나
NFT 소유권을 제공하지 않습니다. 개인 무료팩은 공개 루프 파일럿에서 기본 폐쇄합니다.

## 설치

```powershell
cd C:\Users\Administrator\Desktop\renaiss-tcg-bot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r renaiss_bot\requirements.txt
```

Run the service under a dedicated non-administrator/non-root account. The fixed-frame
Pillow renderer does not start a browser. Keep remote image host and byte allowlists
enabled; a renderer failure falls back to a text card post.

`/flex` is user-invoked and collaboration-room only. Its shared room budget defaults
to 3 posts/day with a 300-second interval (`RENAISS_FLEX_ROOM_DAILY_LIMIT`,
`RENAISS_FLEX_ROOM_COOLDOWN_SECONDS`). Ambiguous deliveries continue to consume
the budget so a retry cannot duplicate a public post.

Partner requests are also admitted through a PostgreSQL-backed cross-instance
start-rate gate plus a per-process semaphore. Pilot defaults are 2 concurrent
requests and at least 500 ms between request starts (`RENAISS_API_MAX_CONCURRENCY`,
`RENAISS_API_MIN_INTERVAL_MS`, `RENAISS_API_QUEUE_WAIT_SECONDS`). Keep these
conservative until the Partner API quota is confirmed.

## 환경변수

`renaiss_bot\.env.example`를 기준으로 루트 `.env` 또는 실행 환경에 값을 넣습니다.
코드는 현재 작업 디렉터리가 아니라 이 standalone 저장소의 루트 `.env`만 자동
탐색합니다. 외부 secret 파일을 쓰는 서비스는 프로세스 환경의 `RENAISS_ENV_FILE`에
경로를 주입하며, 상대경로는 standalone 루트 기준으로 해석됩니다. 명시한 파일을
읽을 수 없으면 ambient 설정으로 계속하지 않고 시작을 거부합니다.

필수:

```text
RENAISS_BOT_TOKEN=...
RENAISS_EXPECTED_BOT_ID=123456789
RENAISS_OFFICIAL_CHAT_ID=-100...
DATABASE_URL=postgresql://user:pw@host:5432/db
```

`RENAISS_EXPECTED_BOT_ID`는 전용 Renaiss 토큰의 `getMe` 숫자 ID입니다. 시작 시
실제 봇 ID가 다르면 DB와 명령을 건드리기 전에 종료합니다. `RENAISS_OFFICIAL_CHAT_ID`는 공개 `c → guess → reveal` 스폰과 Result Bell 대상
Telegram 콜라보 게임방 supergroup입니다. 양수 개인 채팅 ID와 레거시
`RENAISS_QUIZ_CHAT_ID`는 이 게임방을 대신할 수 없습니다. BotFather `/setprivacy`를 Disable하거나 봇을 관리자
로 설정하고, 일반 멤버의 plain `c`가 실제 catch 참여로 기록되는지 확인합니다.

Discord를 같이 실행할 때:

```text
RENAISS_DISCORD_TOKEN=...
RENAISS_EXPECTED_DISCORD_BOT_ID=...
RENAISS_DISCORD_GUILD_ID=...
RENAISS_DISCORD_ALLOW_GLOBAL_SYNC=0
```

Discord 파일럿은 기본적으로 위 guild 하나에만 명령을 sync합니다. guild ID가 없는
전역 sync는 `RENAISS_DISCORD_ALLOW_GLOBAL_SYNC=1`을 별도로 명시한 경우에만 허용됩니다.

선택/기능별 설정:

```text
RENAISS_SKIP_DB=0
RENAISS_BASE_URL=https://www.renaiss.xyz
RENAISS_REFERRAL_CODE=...
RENAISS_REFERRAL_PARAM=ref
RENAISS_API_BASE_URL=...
RENAISS_API_SEARCH_PATH=/v1/search
RENAISS_API_KEY=...
RENAISS_API_SECRET=...
RENAISS_API_ITEM_BY_NO_PATH=
RENAISS_API_EXACT_CONTRACT=
RENAISS_API_EXACT_VALUATION_METHOD=
RENAISS_API_DEFAULT_MARKET_GRADE=PSA 10 Gem Mint
RENAISS_PRICE_CACHE_TTL_MINUTES=10
RENAISS_DAILY_PICK_ENABLED=0
RENAISS_PRIVATE_FREE_PACKS_ENABLED=0
RENAISS_DAILY_PICK_PROBE_CARD_NAME=...
RENAISS_DAILY_PICK_PROBE_SET_NAME=...
RENAISS_DAILY_PICK_PROBE_ITEM_NO=...
RENAISS_COMMAND_DELETE_DELAY_SECONDS=60
RENAISS_FIRST_SPAWN_DELAY_SECONDS=60
RENAISS_SPAWN_CATCH_WINDOW_SECONDS=40
RENAISS_SPAWN_INTERVAL_MIN_SECONDS=7200
RENAISS_SPAWN_INTERVAL_MAX_SECONDS=14400
RENAISS_SPAWN_DAILY_CAP=6
RENAISS_SPAWN_QUIET_START_HOUR_KST=0
RENAISS_SPAWN_QUIET_END_HOUR_KST=9
RENAISS_COHORT_EXPERIMENT_ENABLED=0
RENAISS_COHORT_EXPERIMENT_SALT=...
```

위 값은 무인 파일럿 안전 프로필이며 시즌 공급량이 아니다. TGPoke Season 1의
최상위 방 실측(중앙 스폰 간격 약 30초, 일평균 약 2,530회)에 가까운 공식방
고빈도 프로필은 DB 캐시 manifest와 부하 검증을 끝낸 뒤 아래처럼 명시적으로 켠다.

```env
RENAISS_SPAWN_CATCH_WINDOW_SECONDS=20
RENAISS_SPAWN_INTERVAL_MIN_SECONDS=30
RENAISS_SPAWN_INTERVAL_MAX_SECONDS=30
RENAISS_SPAWN_DAILY_CAP=2880
RENAISS_SPAWN_QUIET_START_HOUR_KST=0
RENAISS_SPAWN_QUIET_END_HOUR_KST=0
```

고빈도 프로필에서 스폰 요청 경로가 카드별 외부 API 응답을 기다리게 해서는 안 된다.
검증 가격·거래량은 사전에 DB에 저장하고 별도 비동기 갱신 작업으로 freshness를
유지해야 한다.

실행 중에는 `renaiss_catalog_cards`의 활성 시즌 카드만 03:00·15:00 KST에 갱신한다.
`RENAISS_CATALOG_REFRESH_LIMIT=1000`이 한 번의 최대 갱신량이며, 다중 프로세스에서는
PostgreSQL lease를 획득한 한 프로세스만 API를 호출한다. 스폰은 저장된 가격 근거와
이미지 URL을 읽고, 이미지 전송은 기존 Telegram `file_id` 캐시를 우선 사용한다.

코호트 실험은 기본 비활성화다. 활성화하면 검증된 가격으로 guess가 가능한 라운드만
`catch-only` 또는 `insight-layer`로 참여 전에 결정적으로 배정하며, 16자 이상의 비공개
salt가 없으면 기존 guess 동작을 유지하고 실험 배정을 기록하지 않는다.

`Daily Market Pick`은 기본적으로 숨겨져 있습니다. API key/secret, 실제 동작이
확인된 구조 식별 경로, known-good probe tuple을 설정하고 봇을 콜라보 게임방 관리자로 만든
뒤 `RENAISS_DAILY_PICK_ENABLED=1`로 요청합니다. 시작 시 그 tuple이 exact/fresh/
confidence/source/허용 URL 게이트와 실응답의 명시적 `median` valuation method를 모두
통과해야 이 프로세스의 신규 Pick이 열립니다. `RENAISS_API_EXACT_VALUATION_METHOD=median`
설정만으로는 근거가 되지 않으며, 승인된 fixture와 실제 응답 필드가 둘 다 필요합니다.
probe가 429·timeout·계약 불일치로 실패하면 Daily Pick만 닫히며 core `c` 게임은 계속
기동합니다. 문서에 적힌 경로 문자열을 실제 probe 없이 복사하면 안 됩니다.

배포 전 read-only preflight에서 `--check-telegram-config`와
`--check-telegram-live`를 함께 사용하면 메시지를 보내지 않고 전용 bot ID, 공식
그룹, 봇 membership을 확인합니다. BotFather privacy와 일반 멤버의 plain `c`
전달은 별도 수동 E2E가 필요합니다.

활성화되면 21:05~24:00 KST에 5분마다 해당 공식 그룹의 **가장 최근 완전 정산
코호트**와 durable outbox를 확인합니다. 정확한 T+24h 판정을 유지하므로 21~24시에
고른 당일 코호트는 다음 날 21시에 전부 정산될 수 없고, 공개 대상은 보통 D-2입니다.
오래된 결과가 뒤늦게 나오지 않도록 최근 2일보다 오래된 코호트는 후보에서 제외합니다.
초기 파일럿은 Telegram에서 공식 그룹 멤버십이 확인된 선택만 집계합니다. 6명 이상·
동일 카드 3명 이상·단독 최다 조건을 만족하면 익명 Crowd Pick을 공개하고, 조건
미달이면 카드명·인원 수 없이 정산 완료만 알리는 privacy-limited Bell을 보냅니다.

가격 갱신 잡은 PostgreSQL 전역 lease와 카드별 lease를 함께 사용합니다. 여러 봇
인스턴스가 동시에 떠도 하나의 API 배치만 실행하며, 429의 Retry-After도 DB에
공유합니다. Partner API의 공유 cooldown은 스폰·팩·`/price`에도 적용되고,
개인 `/price`는 기본 10초 간격으로 제한합니다.

콜라보 게임방에서 처음 `c`를 누른 사용자는 스폰 활성 여부와 무관하게 평생 한 번
`Renaiss Welcome Card`를 받습니다. 이 카드는 가격이 없는 튜토리얼 수집품이며
FMV·Daily Pick·점수·팩 경제에 사용되지 않습니다. 이벤트 원장과 카드 지급은
한 PostgreSQL 트랜잭션으로 처리합니다.

실제 outbound click을 측정하려면 별도 HTTPS 주소와 32자 이상의 비밀키를 설정하고
redirect 서비스를 실행합니다. 설정하지 않으면 모든 버튼은 기존 Renaiss URL로
직접 연결되므로 게임 동작에는 영향이 없습니다.

```powershell
$env:RENAISS_CLICK_TRACKER_PUBLIC_BASE_URL="https://click.example.com"
$env:RENAISS_CLICK_TRACKER_SECRET="replace-with-at-least-32-random-characters"
.\.venv\Scripts\python.exe -m renaiss_bot.referral_server
# or: renaiss_bot\start_renaiss_referral.bat
```

`RENAISS_SKIP_DB=1`은 로컬 샘플 카드풀/렌더링 inspection 전용입니다. Telegram과
Discord 운영은 PostgreSQL 없이 시작하지 않으며, 운영 DB/카탈로그 장애 시 샘플
가격을 실제 컬렉션으로 저장하지 않습니다.

## 실행

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.main
```

Windows 수동 실행용:

```powershell
renaiss_bot\start_renaiss_bot.bat
```

Windows launcher는 저장소의 `.venv`만 사용합니다. 별도 전용 가상환경을 쓰는
서비스라면 `RENAISS_PYTHON_EXE`에 그 환경의 `python.exe` 절대경로를 주입합니다.
관리자 개인 계정의 전역 Python 경로를 launcher에 고정하지 않습니다.
로그는 소스 트리가 아니라 기본 `%ProgramData%\Renaiss\logs`에 기록합니다. launcher는
이 경로를 만들지 않으므로 먼저 전용 서비스 계정이 쓸 수 있게 준비해야 합니다.
다른 절대 로컬 경로는 launcher의 프로세스 환경 `RENAISS_LOG_DIR`로 지정합니다.
로그는 기본 10 MiB active 1개와 백업 5개로 제한됩니다. 크기와 백업 수를 바꿀 때는
launcher 프로세스 환경의 `RENAISS_LOG_MAX_BYTES`, `RENAISS_LOG_BACKUPS`를 사용합니다.
세부 ACL과 회전 실패 정책은 `RUNBOOK.md`를 따릅니다.
Windows Telegram 파일럿의 Task Scheduler·단일 인스턴스·rollback 기준은
[`ops/README.md`](ops/README.md)에 있습니다. 문서 추가만으로 task가 등록되지는 않습니다.

Discord adapter 실행:

```powershell
.\.venv\Scripts\python.exe -m pip install -r renaiss_bot\requirements.txt -r renaiss_bot\requirements-discord.txt
.\.venv\Scripts\python.exe -m renaiss_bot.adapters.discord.main
```

## 명령

- `/start` : 홈 안내
- `/mycards` : 컬렉션 요약, 등급 분포, 상위 카드, 최근 획득 (경쟁 점수와 분리)
- `/price Charizard` : 개인 채팅에서 Pokemon 가격 확인 (그룹에서는 DM으로 이동)
- `/price one_piece_tcg luffy` : One Piece 가격 확인
- `/market` : 검증된 카드 중 오늘의 Daily Market Pick 1장 선택 (21:00~24:00 KST), 최근 완전 정산된 개인 결과 확인
- `/sets` : 지원 카테고리
- `c` (콜라보 게임방) : 블라인드 스폰 포획 추첨 참가 + 시세 4지선다 추측

레거시 RP·프리미엄팩 경제는 제품에서 폐기되었으며 환경변수로 다시 켤 수 없다.
개인 `/open`·`/pack`은 `RENAISS_PRIVATE_FREE_PACKS_ENABLED=1`을 명시한 격리 실험에서만
노출됩니다. 기본값에서는 명령 목록과 홈에서 숨고, 오래된 링크나 명령 호출도 DB 작업
전에 안전한 폐쇄 안내로 종료됩니다.

실제 PostgreSQL 동시성 검증은 선택 실행입니다. 무료팩·스폰·정산뿐 아니라 가격
갱신 lease, API cooldown, 첫-c 스타터, 일일 Flex slot, Result Bell outbox의 다중
worker 격리와 실패 rollback도 확인합니다.

```powershell
$env:RENAISS_REQUIRE_POSTGRES_TESTS="1"
.\.venv\Scripts\python.exe -m pytest tests/integration -m postgres --postgres-integration -q
```

`RENAISS_TEST_DATABASE_URL`이 없으면 정상 실행 중인 Docker daemon에서 임시
`postgres:16-alpine` 컨테이너를 사용합니다. 둘 다 없으면 로컬에서는 skip하며,
CI에서는 `RENAISS_REQUIRE_POSTGRES_TESTS=1`로 미실행을 실패 처리할 수 있습니다.
호환용 DB 테이블과 내부 코드는 기존 데이터 보호를 위해 당분간 유지한다.

별도 Daily Price Quiz도 블라인드 스폰의 FMV 추측과 중복되므로 기본 비활성화다.
별도 Daily Quiz는 가격 추측과 중복되어 현재 파일럿에서는 재활성화할 수 없다.

## Discord 명령

- `/price query:one_piece_tcg luffy`
- `/mycards`
- `/sets`

Discord `/open`도 같은 명시적 개인팩 실험 플래그를 켠 프로세스에서만 sync됩니다.

## 구조

- `services/card_pool.py` : 운영 DB/카탈로그 우선; 샘플 fallback은 명시적 local inspection에서만 사용
- `renaiss_catalog_cards` : One Piece/기타 Renaiss 카드용 독립 카탈로그 테이블
- `services/pack_rules.py` : 시즌2 카드팩 확률/슬롯 구조의 Renaiss 전용 사본
- `services/client.py` : Renaiss API search adapter
- `services/pricing.py` : API 우선 가격 조회, 캐시, fallback
- `renderers/overlay.py` : 대표 카드 + Renaiss FMV 상단바 PNG
- `database/schema.py` : Renaiss 전용 이벤트/컬렉션/가격 스냅샷 테이블
- `adapters/discord/` : Discord slash command adapter

## 카탈로그 Import

Renaiss API나 CSV 변환 결과를 JSON 배열로 만든 뒤 독립 카탈로그에 넣을 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.tools.import_catalog_json .\onepiece_cards.json --category one_piece_tcg --dry-run
.\.venv\Scripts\python.exe -m renaiss_bot.tools.import_catalog_json .\onepiece_cards.json --category one_piece_tcg
```

일반 JSON과 Ed25519 서명 manifest 가격은 모두 collection-only다. 서명은 파일
무결성과 배포 주체만 증명하며 현재 시세를 증명하지 않는다. 경쟁용 `exact` 근거는
별도의 live Partner API refresh가 성공해 `official-api-import` provenance를 만든 뒤에만
생긴다. 현재 그 경로는 POKARD universe를 읽어 Partner API로 각 카드를 갱신하는
`python -m renaiss_bot.tools.import_from_pokard ...`이다. signer가 trust store에서
제거되면 기존 서명 근거도 즉시 신뢰되지 않는다. 외부 ID는 category/provider
namespace로 저장하고 구조 충돌은 덮어쓰지 않고 import 전체를 rollback한다.
`python -m renaiss_bot.tools.manifest_preflight ...`는 DB 접속 전에 서명과 데이터 품질을
검증한다.

live refresh 뒤 검증 가격 메타데이터와 전체 identity 상태는 DB를 변경하지 않는 감사
명령으로 확인한다. 서명 manifest만 import한 상태에서는 `--require-eligible 20`이
실패하는 것이 정상이다.

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.tools.catalog_audit --category pokemon_tcg --pool-source actual --require-eligible 20
```

## 포함 문서

- `docs/GAME_PHILOSOPHY.md` — 르네이스 전용 최상위 제품 철학 (`c` 영어판 기준)
- `docs/RENAISS_TCG_PRICE_OVERLAY_PLAN_2026-06-28.md`
- `docs/RENAISS_BOT_SEPARATION_PLAN_2026-06-28.md`
- `docs/renaiss_overlay_render_examples.html`
- `docs/droproom_renaiss_design.html`
