# Renaiss Bot Runbook

> Renderer safety: the default renderer uses a fixed Pillow frame and never starts
> Chromium. Keep image host/size allowlists enabled; rendering failures use the
> text fallback. Telegram `file_id` values are cached per expected bot id.
>
> Public `/flex` defaults to a shared 3-post daily room cap and a 300-second
> minimum interval. Review `RENAISS_FLEX_ROOM_DAILY_LIMIT` and
> `RENAISS_FLEX_ROOM_COOLDOWN_SECONDS` before the pilot; do not remove both
> safeguards.
>
> Partner API admission defaults to 2 concurrent requests/process and one
> cross-instance request start per 500 ms. Keep the PostgreSQL request gate
> enabled until Renaiss confirms a higher quota.
>
> Canonical runtime source: `renaiss-tcg-bot`. Do not start the older
> `pokemon-bot\renaiss_bot` copy. Rotate or replace its Telegram credential
> before production so an old poller cannot claim the same bot.
> Runtime dotenv discovery is pinned to the canonical root `.env`. A service
> may inject `RENAISS_ENV_FILE` for an external secret file; relative paths are
> resolved from the canonical root, never from its current working directory.

## Windows 실행기 로그와 최소 권한

Telegram 파일럿의 immutable release, Task Scheduler, rollback 전체 절차는
[`ops/README.md`](ops/README.md)를 함께 따른다. 이 저장소는 task를 자동 등록하지 않는다.

세 Windows launcher는 로그를 소스 트리에 쓰지 않는다. `RENAISS_LOG_DIR`이 없으면
`%ProgramData%\Renaiss\logs`를 사용하며, 이 디렉터리를 자동 생성하지 않는다.
launcher를 등록하기 전에 운영자가 절대 로컬 경로를 만들고 서비스 계정에만
`Modify` 권한을 부여한다. 상대경로, 소스 트리 안의 경로, 없는 디렉터리, 로그 파일을
쓸 수 없는 계정은 Python 프로세스를 시작하기 전에 exit code 2로 거부된다.

예를 들어 전용 계정이 `CONTOSO\renaiss-svc`라면 관리자 PowerShell에서 다음처럼
로그 디렉터리를 준비한다. 실제 계정명으로 바꾸기 전에는 실행하지 않는다.

```powershell
$logDir = "$env:ProgramData\Renaiss\logs"
$serviceAccount = "CONTOSO\renaiss-svc"
New-Item -ItemType Directory -Force -Path $logDir
icacls $logDir /inheritance:r
icacls $logDir /grant:r `
  "*S-1-5-18:(OI)(CI)(F)" `
  "*S-1-5-32-544:(OI)(CI)(F)" `
  "${serviceAccount}:(OI)(CI)(M)"
```

다른 로그 볼륨을 사용할 때만 Task Scheduler나 서비스의 **프로세스 환경**에
`RENAISS_LOG_DIR` 절대경로를 넣는다. launcher가 애플리케이션 `.env`를 읽기 전에
로그를 열기 때문에 `.env`의 같은 키는 적용되지 않는다. 소스와 `.venv`에는 서비스
계정의 `Read & Execute`만 주고 `Modify`를 주지 않는다. launcher는 Python을 `-B`로
실행해 소스 트리에 `__pycache__`를 만들지 않는다.

launcher의 bounded log runner 기본값은 파일당 10 MiB와 백업 5개다.
`RENAISS_LOG_MAX_BYTES=10485760`, `RENAISS_LOG_BACKUPS=5`이며 active 1개와 백업
5개를 합쳐 서비스별 최대 약 60 MiB다. 세 서비스를 모두 켜면 기본 최대 약
180 MiB이므로 볼륨 경보는 이 상한과 여유 공간을 함께 기준으로 잡는다. 두 값도
`.env`가 아니라 launcher의 프로세스 환경에 넣는다. 허용 범위는 파일당
64 KiB~1 GiB, 백업 0~20개이며 범위를 벗어나면 서비스 모듈을 실행하지 않는다.

runner는 별도 wrapper child를 만들지 않고 같은 PID에서 서비스 모듈을 실행한다.
파일럿 전 stop rehearsal에서 parent PID, 실행 경로, 서비스 계정을 대조한다.
Python은 계속 `-u`로 실행하고 fd 1과 2를 하나의
pipe로 합쳐 Python logging, traceback, native stdout/stderr를 한 writer가 기록한다.
writer만 active 파일을 열며 회전 전에 닫기 때문에 Windows에서 실행 중인 서비스가 자기
로그 rename을 막지 않는다.

active와 각 백업은 큰 단일 출력 chunk도 설정 크기를 넘지 않게 나눠 기록한다. 기존
로그가 이미 상한을 넘으면 최신 tail만 남긴 뒤 시작하고, 설정 개수를 넘는 숫자 백업은
정리한다. 백업 rename/delete가 외부 viewer나 scanner의 Windows sharing lock 때문에
계속 실패하면 active 파일을 닫은 상태에서 truncate하고 경고 marker를 남겨, 이력 일부를
잃더라도 용량 상한과 서비스 가용성을 유지한다. active 쓰기 자체가 실패하면 로그 없이
서비스를 계속하지 않고 action을 exit code 74로 중단한다.

각 로그의 `*.log.lock`은 같은 외부 로그 디렉터리에 있는 1-byte 잠금 파일이다. 같은
서비스 로그를 두 runner가 동시에 회전하지 못하게 하며 정상 종료 뒤 파일은 남아 있어도
잠금은 해제된다. lock 파일을 삭제해 실행 중인 잠금을 우회하지 않는다.

Pillow 프레임과 완성 PNG의 bounded cache는 프로세스 메모리만 사용한다. Telegram
`file_id` 캐시는 PostgreSQL에 bot id별로 저장한다. secret 파일은 소스 밖의 외부
경로에 두고 서비스 계정에는 `Read`만 허용한다.

## 로컬 스모크 테스트

```powershell
$env:RENAISS_SKIP_DB="1"
.\.venv\Scripts\python.exe -m compileall renaiss_bot
@'
import asyncio
from renaiss_bot.services.pack import open_pack
from renaiss_bot.renderers.overlay import render_overlay_card

async def main():
    result = await open_pack(None, count=30)
    png = await render_overlay_card(result.best_card, result.best_price)
    print(result.pack_count, len(result.cards), result.best_card.card_name, result.best_price.status, len(png))

asyncio.run(main())
'@ | .\.venv\Scripts\python.exe -
```

정상 기준:

```text
30 300 <best card name> <match status> <png byte size>
```

## API Mock 스모크

```powershell
$env:RENAISS_SKIP_DB="1"
$env:RENAISS_PRICE_CACHE_TTL_MINUTES="0"
$env:RENAISS_API_MOCK_JSON='{"results":[{"name":"Charizard ex","set_code":"SV4a","collector_number":"349/190","grade":"PSA 10","fmv_usd":430,"change_7d_pct":12.4,"url":"https://www.renaiss.xyz/assets/charizard-ex"}]}'
.\.venv\Scripts\python.exe -c "import asyncio; from renaiss_bot.services.models import CardIdentity; from renaiss_bot.services.pricing import fetch_price; p=asyncio.run(fetch_price(CardIdentity(category='pokemon_tcg', card_name='Charizard ex', set_code='SV4a', collector_number='349/190', grade='PSA 10'))); print(p.status, p.source, p.fmv_usd, p.change_7d_pct)"
```

`RENAISS_API_MOCK_JSON` 응답은 항상 non-competitive mock으로 표시되며 Daily Pick과
점수형 가격 추측을 열 수 없다. 운영 환경에서는 반드시 unset한다.

## 배포 전 체크

운영 DB를 쓰는 release 순서는 **백업·복구 절차 확인 → 봇/잡 중지 → 스키마 준비 →
read-only gate → 단일 인스턴스 시작**이다. 스키마 준비는 canonical root `.env` 또는
`RENAISS_ENV_FILE`만 읽으며, `--apply` 없이는 쓰지 않고 mock/skip DB 설정을 거부한다.

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.tools.prepare_database --print-target-fingerprint
# Copy the printed digest to RENAISS_EXPECTED_DATABASE_FINGERPRINT, then:
.\.venv\Scripts\python.exe -m renaiss_bot.tools.prepare_database --apply
```

이 명령이 `PASS`여도 배포 승인은 아니다. 바로 아래 release gate와 live preflight가
모두 통과한 뒤에만 서비스를 시작한다.

통합 release gate는 단계별 요구사항을 한 번에 검사한다. 기본값은 clean worktree와
격리된 PostgreSQL 통합 테스트를 강제하며, 운영 `DATABASE_URL`을 테스트 DB로 재사용하지
않는다.

```powershell
# collection-only 파일럿
.\.venv\Scripts\python.exe -m renaiss_bot.tools.release_gate --stage collection

# 검증 가격/Partner API가 포함된 market 파일럿
.\.venv\Scripts\python.exe -m renaiss_bot.tools.release_gate --stage market `
  --card-name "..." --set-name "..." --item-no "..." --min-eligible 20

# D1/D7 및 첫-c 성공 기준까지 통과한 졸업 판정
.\.venv\Scripts\python.exe -m renaiss_bot.tools.release_gate --stage graduation `
  --card-name "..." --set-name "..." --item-no "..." --min-eligible 20
```

`--allow-dirty`와 `--skip-test-suite`는 개발 중 진단에만 사용한다. 이 옵션을 사용한
결과는 배포 승인으로 인정하지 않는다.

GitHub Actions의 `pytest-postgres` 작업은 PostgreSQL 16 서비스에서 전체 테스트를
`--postgres-integration`으로 실행한다. 이 작업이 통과하지 않으면 clean schema,
반복 migration, 동시성 보장을 검증한 것으로 간주하지 않는다. 로컬에서 같은 검증을
실행하려면 격리된 테스트 DB를 지정한다.

```powershell
$env:RENAISS_TEST_DATABASE_URL='postgresql://.../renaiss_test'
$env:RENAISS_REQUIRE_POSTGRES_TESTS='1'
.\.venv\Scripts\python.exe -m pytest tests -q --postgres-integration
```

- `RENAISS_BOT_TOKEN`이 TGPOKE 본봇 토큰과 다른지 확인하고, 전용 봇의 `getMe`
  숫자 ID를 `RENAISS_EXPECTED_BOT_ID`에 고정
- `RENAISS_OFFICIAL_CHAT_ID`가 실제 파일럿 supergroup인지 확인하고, 테스트 메시지로
  봇의 전송·메시지 수정·`getChatMember` 권한을 검증
- BotFather `/setprivacy`를 Disable하거나 봇을 관리자로 설정하고, 다른 일반 멤버가
  보낸 plain `c`가 handler에 도달해 catcher 수가 증가하는지 반드시 E2E 확인
- Discord를 켤 때 `RENAISS_DISCORD_TOKEN`이 별도 Discord bot token인지 확인
- 전용 bot user ID를 `RENAISS_EXPECTED_DISCORD_BOT_ID`에 고정하고
  `RENAISS_DISCORD_GUILD_ID`를 넣어 파일럿 서버 하나에만 slash command를 sync
- 전역 sync는 별도 변경 승인 뒤 `RENAISS_DISCORD_ALLOW_GLOBAL_SYNC=1`로만 허용
- guild 모드 시작 시 기존 원격 global command를 읽기 전용 검사한다. 하나라도
  남아 있으면 시작이 중단되므로 Discord Developer Portal/승인된 운영 절차로 먼저 제거
- `RENAISS_REFERRAL_CODE`가 실제 referral 코드인지 확인
- 클릭 KPI를 사용할 때 tracker public URL이 HTTPS인지, secret이 32자 이상인지 확인
- `RENAISS_API_BASE_URL`, `RENAISS_API_KEY`, `RENAISS_API_SECRET`이 공식 Partner 값인지 확인
- 구조 조회 경로를 직접 probe한 뒤에만 `RENAISS_API_ITEM_BY_NO_PATH`를 설정한다. 2026-07-11 기준 문서의 `/v1/index/item-by-no`는 실제 API에서 404였고 공개 OpenAPI에도 없었다.
- Daily Pick은 exact identity, freshness, numeric confidence, source count와 명시적 `median` valuation method가 실제 응답에서 확인된 뒤에만 `RENAISS_DAILY_PICK_ENABLED=1`로 연다.
- Daily Pick을 요청할 때는 봇을 콜라보 게임방 관리자로 두고 `RENAISS_DAILY_PICK_PROBE_CARD_NAME`,
  `RENAISS_DAILY_PICK_PROBE_SET_NAME`, `RENAISS_DAILY_PICK_PROBE_ITEM_NO`에 실제
  known-good tuple을 넣는다. 시작 probe 실패 시 신규 Pick만 닫히는 것이 정상이다.
- 운영 DB를 쓸 때 `DATABASE_URL`을 넣고 시작 로그에 `Renaiss DB tables ready.`가 찍히는지 확인
- `RENAISS_TELEGRAM_LOCK_DATABASE_URL`은 `DATABASE_URL`과 같은 PostgreSQL cluster 및
  database의 direct PostgreSQL 또는 session-mode endpoint인지 별도 확인. PgBouncer
  transaction mode는 금지하며 모든 Renaiss poller가 같은 lock endpoint를 사용해야 한다.
- 파일럿 호스트의 sleep/hibernate를 승인된 전원 정책에서 비활성화하고 실제 적용 상태를
  확인. Task Scheduler의 trigger/재시작 설정은 잠든 호스트에서 watchdog과 session probe가
  멈추는 위험을 대신 해결하지 않는다.
- PostgreSQL TLS 인증서 검증은 기본 활성화다. `RENAISS_DB_SSL_INSECURE=1`은 로컬 임시 검증 외에는 사용하지 않는다.
- 기본 파일럿에서 `RENAISS_PRIVATE_FREE_PACKS_ENABLED=0`이고 `/open`·`/pack`이 명령 목록과 홈에서 숨으며, 직접 호출도 DB 접근 전 폐쇄 안내로 끝나는지 확인
- 별도 승인된 개인팩 실험에서만 `RENAISS_PRIVATE_FREE_PACKS_ENABLED=1`로 켠 뒤 `/open 5`의 일일 한도와 카테고리 격리를 확인
- `/mycards`가 고유 카드, 총 보유량, 등급 분포, 상위 카드, 최근 획득을 보여주는지 확인

배포 게이트는 다음 read-only 명령으로 한 번에 확인한다.

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.tools.preflight `
  --check-telegram-config `
  --check-telegram-live `
  --require-daily-pick `
  --check-api `
  --card-name "Charizard" `
  --set-name "Base Set" `
  --set-code "BS" `
  --item-no "4/102" `
  --language "English" `
  --grade "RAW"
```

`--check-telegram-live`는 `getMe`, `getChat`, `getChatMember`만 호출하며 메시지나
명령을 전송·수정하지 않는다. BotFather privacy 상태는 Bot API로 확인할 수 없으므로,
마지막으로 일반 멤버가 보낸 plain `c`가 실제 handler에 도달하는지 수동 E2E한다.

PostgreSQL 필수 테이블과 exact/fresh/confidence/source/source URL/명시적 Median 조건이 모두
`PASS`가 아니면 `RENAISS_DAILY_PICK_ENABLED=1`로 바꾸지 않는다.
DB preflight는 두 DSN에서 transaction-scoped advisory lock만 사용해, `DATABASE_URL`
쪽 contender가 lock DSN의 같은 잠금에 막히고 transaction 종료 뒤 다시 획득하는지 검사한다.
따라서 서로 다른 cluster/database를 가리키는 오설정은 실패하며 probe lock도 고아 session
lock으로 남지 않는다. 이 검사는 외부 pooler의 동작 모드를 증명하지 않으므로
direct/session-mode endpoint라는 운영자 증거는 여전히 필수다.

Telegram 프로세스는 `RENAISS_TELEGRAM_LOCK_DATABASE_URL`의 전용 PostgreSQL 연결에서
`renaiss:telegram_poller:<bot id>` session advisory lock을 획득하고 종료까지 연결을
유지한다. 같은 DB와 bot ID를 쓰는 두 번째 poller는 polling 전에 중단된다. 전용 연결은
10초마다 probe하며 연결 또는 lock session을 잃으면 polling을 닫고 exit 75로 끝나
Task Scheduler의 실패 재시작 대상이 된다. event loop heartbeat가 45초 멈추면 graceful
stop을 요청하고 60초까지 회복·종료되지 않으면 같은 PID를 hard exit 75로 끝낸다.
transaction-pooling endpoint에서는 session lock 소유권이 client connection과 일치하지
않으므로 시작 설정으로 금지한다. 이 fence는 다른 DB를 쓰는 레거시 poller까지 볼 수 없으므로
토큰 기준 process/task inventory와 plain `c` E2E를 대체하지 않는다. 같은 PID의 hard
exit와 별개로 stop rehearsal에서 poller 종료와 advisory lock 해제를 확인한다.

## 파일럿 운영 가시성

전체 퍼널, click, D1/D7, overdue Pick은 다음 명령으로 확인한다.

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.tools.pilot_report --days 14
```

리포트의 `Public Result Bell`에서 `delivery_unknown`이 하나라도 보이면 자동
재전송하지 않는다. Telegram 전송 성공 여부가 모호한 상태이므로 공식방을 확인한
뒤 운영자가 아래 reconciliation 절차로 판단한다. 공개 기준 미달 코호트는 카드명과
인원 수를 숨긴 privacy-limited Bell로 일일 리듬만 유지한다.

`Price refresh lease`의 `next`가 장시간 과거인데 갱신이 없다면 잡 등록과 DB 연결을
확인한다. `rate_limited` 상태에서는 Retry-After와 정상 cadence 중 더 늦은 시각까지
모든 인스턴스가 API 호출을 멈춘다.

`Partner API cooldown`의 `blocked until`이 미래라면 스폰·팩·`/price`·가격 갱신이
같은 429 backoff를 공유하는 정상 상태다. 원인을 제거하지 않고 DB 행을 수동 삭제해
쿼터 보호를 우회하지 않는다.

최근 7일 퍼널은 `renaiss_events`에서 바로 확인한다.

```sql
SELECT event_name, COUNT(*) AS events, COUNT(DISTINCT user_id) AS users
FROM renaiss_events
WHERE created_at >= now() - interval '7 days'
GROUP BY event_name
ORDER BY event_name;
```

정산 대기 카드가 계속 쌓이면 API freshness 또는 구조 식별 문제로 본다.

```sql
SELECT
  COUNT(*) AS overdue_picks,
  MIN(settles_at) AS oldest_due,
  COUNT(*) FILTER (WHERE EXISTS (
    SELECT 1 FROM renaiss_market_price_snapshots s
    WHERE s.board_card_id = p.board_card_id
      AND s.pick_eligible = TRUE
      AND s.captured_at >= p.settles_at
      AND s.price_updated_at >= p.settles_at
  )) AS ready_but_unsettled
FROM renaiss_market_picks p
WHERE settles_at <= now()
  AND settlement_snapshot_id IS NULL;
```

실 PostgreSQL 배포 전 검증:

```powershell
$env:RENAISS_REQUIRE_POSTGRES_TESTS="1"
.\.venv\Scripts\python.exe -m pytest tests/integration -m postgres --postgres-integration -q
```

이 테스트는 스키마 2회 적용, 동시 무료팩 quota/finalize, 동시 스폰 지급,
8-worker Daily Pick 정산, 전역 refresh lease/API cooldown, 첫-c 스타터, 일일 Flex
reservation, 채팅별 Result Bell outbox claim/idempotency와 중간 실패 rollback을
검사한다. 운영 DB가 아닌 전용 테스트 DSN 또는 임시 Docker 컨테이너에서만 실행한다.

`Daily Pick refresh rate-limited` 또는 `unsafe configuration` 로그가 한 번이라도
나오면 신규 Pick을 열지 않고 API 상태부터 확인한다.

실제 Renaiss outbound click은 tracker가 켜진 경우에만 집계된다.

```sql
SELECT source, COUNT(*) AS clicks, COUNT(DISTINCT user_id) AS unique_users
FROM renaiss_referral_clicks
WHERE clicked_at >= now() - interval '7 days'
GROUP BY source
ORDER BY clicks DESC;
```

Telegram 공개 그룹의 URL 버튼은 클릭한 사용자 ID를 전달하지 않으므로 spawn/quiz
클릭은 `user_id IS NULL`인 aggregate click이다. `/price`, `/open`, Daily Pick DM,
Discord 개인 응답은 사용자 단위 unique click을 측정할 수 있다.

tracker 프로세스는 봇과 독립적으로 실행한다. supervisor의 생존 확인에는 DB와
무관한 `/livez`를 사용하고, 트래픽 투입 전 DB 준비 확인에는 `/readyz`를 사용한다.
기존 `/health`는 `/readyz` 호환 별칭이다. DB가 잠시 끊겨도 서명된 fallback redirect는
계속 동작하므로 `/readyz` 503만으로 프로세스를 재시작해 fallback까지 끊지 않는다.

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.referral_server
# or: renaiss_bot\start_renaiss_referral.bat
```

## 카탈로그 Import

쓰기 import에서 `--env`를 지정하면 그 경로는 canonical standalone 루트 기준이다.
파일이 없거나 비어 있으면 중단하며, 실제 쓰기에는 파일 자체의 `DATABASE_URL`이
필수다. ambient DB를 빌려 쓰거나 `${...}` 보간으로 다른 환경을 선택하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.tools.import_catalog_json .\onepiece_cards.json `
  --category one_piece_tcg `
  --dry-run

.\.venv\Scripts\python.exe -m renaiss_bot.tools.import_catalog_json .\onepiece_cards.json --category one_piece_tcg
```

일반 JSON import의 가격은 항상 collection-only다. Renaiss 제공 manifest는
정확한 파일 바이트에 대한 Ed25519 detached signature와
공개키를 함께 검증해야 한다. Renaiss에서 별도 채널로 확인한 공개키 DER SHA-256
지문을 `RENAISS_MANIFEST_PUBLIC_KEY_SHA256`에 먼저 고정한다. 교차 용도 서명을
막기 위해 서명 대상은 `renaiss-collector-catalog-manifest:v1\\0` 컨텍스트와 원본
바이트를 결합한 값이다. 운영자가 승인한 파일의 SHA-256도
`RENAISS_MANIFEST_SHA256`에 고정해야 하며, 키 서명이 유효해도 다른 과거 파일은
import하지 않는다. 서명 매니페스트는 현재 collection-only이고 경쟁 가격으로
승격되지 않는다.

Partner `item-by-no` 응답 fixture와 필드 계약을 공식 확인하기 전에는
`RENAISS_API_EXACT_CONTRACT`와 `RENAISS_API_EXACT_VALUATION_METHOD`를 비워 둔다.
확인된 v1 fixture가 경쟁 가격 필드를 명시적으로 Median으로 표기하고 계약 테스트가
통과한 배포에서만 각각 `item-by-no-v1`, `median`으로 설정한다. 환경변수는 출처 근거를
대신하지 않는다. 값이 없거나 다르거나 실제 응답 method가 Median이 아니면
structural 응답도 candidate로 남고 Daily Pick admission은 열리지 않는다.
키 교체 시에는 `old_fingerprint,new_fingerprint`처럼 두 지문을 잠시 함께 허용하고,
새 manifest 전환이 끝난 뒤 이전 지문을 제거한다. 제거된 키로 서명된 기존 catalog
가격은 다음 조회부터 신뢰되지 않는다. 서명은 파일 무결성과 배포 주체만 증명하며
현재 가격을 증명하지 않는다. 따라서 서명 manifest는 항상 collection-only이고,
경쟁용 근거는 별도의 live Partner API refresh가 성공한 뒤에만 생긴다.

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.tools.import_catalog_json .\renaiss_verified_cards.json `
  --category pokemon_tcg `
  --signature .\renaiss_verified_cards.sig `
  --public-key .\renaiss_manifest_public.pem `
  --dry-run
```

서명 검증 후 출력의 `evidence=signed`를 확인한 다음에만 `--dry-run`을 제거한다.
JSON 파일의 공백이나 줄바꿈까지 바뀌면 서명이 무효가 되는 것이 정상이다.

DB 접속 전 서명과 데이터 품질을 확인하려면 offline preflight를 먼저 실행한다.

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.tools.manifest_preflight .\renaiss_verified_cards.json `
  --signature .\renaiss_verified_cards.sig `
  --public-key .\renaiss_manifest_public.pem `
  --category pokemon_tcg `
  --require-eligible 0
```

서명, 고정 signer 지문, 카드 identity와 가격 메타데이터를 검사한다. 이 offline
단계의 `--require-eligible`은 반드시 `0`으로 둔다. 서명 파일만으로 경쟁 eligibility를
주장하지 않는다.

POKARD universe를 사용하는 Pokémon 카드는 VM에서 live Partner refresh를 수행한다.
이 재임포트는 기존 가격 근거를 보존하면서 과거 행의 variation metadata도 backfill한다.

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.tools.import_from_pokard --series base1 --max-cards 200 --sleep 0.5
```

다른 provider/category는 동등한 live refresh 도구가 생기기 전까지 collection-only다.

live refresh 후에만 가격 추측에 사용할 수 있는 검증 카드 수를 읽기 전용으로 확인한다.

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.tools.catalog_audit `
  --category pokemon_tcg `
  --pool-source actual `
  --require-eligible 20
```

`verified eligible`은 `price_status=exact`, 승인된 Renaiss source, set/collector
identity, numeric confidence, source count, HTTPS source URL, 48시간 freshness를 모두
통과한 카드다. 기준 미달 카드는 계속 수집용으로만 사용된다.
`resolved_source=sample`은 명시적 local inspection에서만 정상이다. 운영에서는
`unavailable` 또는 카탈로그 부족을 실패로 보고 샘플 가격을 사용자 컬렉션에 넣지 않는다.

입력 JSON은 배열이어야 합니다. `name`, `id`, `set_code`, `collector_number`, `grade`, `image_url`, `fmv_usd` 같은 필드를 인식합니다.

## Discord 실행

```powershell
.\.venv\Scripts\python.exe -m pip install -r renaiss_bot\requirements.txt -r renaiss_bot\requirements-discord.txt
.\.venv\Scripts\python.exe -m renaiss_bot.adapters.discord.main
```

Windows 수동 실행:

```powershell
renaiss_bot\start_renaiss_discord.bat
```

## Result Bell `delivery_unknown` 조정

`delivery_unknown`은 Telegram 전송 성공 여부가 모호하다는 뜻이다. 자동 재전송하지
말고 먼저 파일럿 리포트에서 outbox ID, 공식 채팅, 코호트 날짜, 만료 시각과 오류를
확인한다.

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.tools.pilot_report --days 14
.\.venv\Scripts\python.exe -m renaiss_bot.tools.reconcile_result_bell show 9
```

공식 채팅에서 실제 메시지를 찾았다면 그 Telegram message ID를 기록해 `sent`로
조정한다. 이 작업은 원래 전송 창이 만료된 뒤에도 가능하다.

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.tools.reconcile_result_bell mark-sent 9 `
  --telegram-message-id 777 `
  --operator "oncall@example.com" `
  --note "Confirmed in the official chat"
```

메시지가 전송되지 않았음을 확실히 확인한 경우에만 원래 `expires_at` 이전에
`retryable`로 되돌린다. 만료된 행의 retry는 CLI가 거부한다. 전송 여부가 계속
모호하면 `delivery_unknown`을 유지한다.

```powershell
.\.venv\Scripts\python.exe -m renaiss_bot.tools.reconcile_result_bell retry 9 `
  --operator "oncall@example.com" `
  --note "Confirmed absent from the official chat"
```

두 변경 모두 현재 상태가 정확히 `delivery_unknown`인 행만 잠금 후 변경하며,
상태 변경과 `daily_pick_result_reconciled` 감사 이벤트가 한 DB 트랜잭션으로
커밋된다. Telegram 관리자 명령은 제공하지 않는다.

## 확장 순서

1. Telegram P0: 공개 `c → hidden-price guess → verified reveal → talk`
2. 운영 DB 카드풀과 Renaiss exact/fresh/confidence/source gate 확인
3. Daily Pick·Result Bell 소규모 공식방 파일럿
4. One Piece TCG 카테고리와 개인 컬렉션 보조 흐름 검증
5. 7d 결과·Rookie League는 T+24h 리텐션 가설이 확인된 뒤 추가
6. Discord adapter는 Telegram 핵심 루프가 안정된 뒤 확장
