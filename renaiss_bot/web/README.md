# Renaiss collaboration web companion

이 디렉터리가 `tgpoke.com/renaiss`의 소스 오브 트루스다. TGPoke 저장소는 메뉴 링크만
유지하며, 도감 데이터·Telegram 인증·정적 자산·API를 소유하지 않는다.

## Local preview

미리보기는 명시적으로 켠 경우에만 가짜 카드 5장과 테스트 로그인을 제공한다.

```powershell
$env:RENAISS_WEB_PREVIEW='1'
$env:RENAISS_WEB_HOST='127.0.0.1'
$env:RENAISS_WEB_PORT='18083'
python -m renaiss_bot.web.app
```

열기: `http://127.0.0.1:18083/renaiss/pokedex`

`RENAISS_WEB_PREVIEW=1`은 공개 배포에 사용할 수 없다. 실제 서비스는 최소 10장의 활성
`renaiss_catalog_cards`와 PostgreSQL 준비 상태를 확인하고, 부족하면 TGPoke의 일반 카드나
mock 데이터로 채우지 않는다.
미리보기는 실수로 Tunnel에 연결되지 않도록 운영 포트 `18082`에서 시작 자체를 거부한다.

## Production requirements

- `RENAISS_ENV_FILE`: 저장소 밖의 웹 전용 env 파일 절대경로. 기본값은
  `%ProgramData%\Renaiss\secrets\web.env`
- `DATABASE_URL`: Renaiss 봇과 같은 database를 가리키는 **별도 SELECT-only 계정**
- `RENAISS_EXPECTED_DATABASE_FINGERPRINT`: `DATABASE_URL`의 host/port/database를 고정하는
  SHA-256 digest
- `RENAISS_WEB_SESSION_SECRET`: 32자 이상의 별도 랜덤 secret
- `RENAISS_EXPECTED_BOT_ID`: 전용 Renaiss Telegram bot의 숫자 ID
- `RENAISS_OFFICIAL_GROUP_URL`: 사용자가 `c`를 입력할 공식 Telegram 게임방 URL
- `RENAISS_TELEGRAM_OIDC_CLIENT_ID`
- `RENAISS_TELEGRAM_OIDC_CLIENT_SECRET`
- `RENAISS_TELEGRAM_OIDC_REDIRECT_URI=https://tgpoke.com/renaiss/api/auth/telegram/callback`

[`web.env.example`](web.env.example)의 키만 외부 env 파일에 복사한다. 웹 프로세스는
Telegram bot token, Renaiss Partner API key/secret, Discord token, click-tracker secret을
받으면 시작을 거부한다. DB role은 아래 세 table에만 `SELECT`가 있어야 하고 table 쓰기 및
`public` schema의 `CREATE` 권한이 있으면 역시 시작을 거부한다.

```sql
CREATE ROLE renaiss_web_reader LOGIN NOINHERIT PASSWORD 'replace-with-a-random-secret';
ALTER ROLE renaiss_web_reader
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT CONNECT ON DATABASE renaiss TO renaiss_web_reader;
GRANT USAGE ON SCHEMA public TO renaiss_web_reader;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM renaiss_web_reader;
GRANT SELECT ON TABLE
  public.renaiss_catalog_cards,
  public.renaiss_user_cards,
  public.renaiss_events
TO renaiss_web_reader;
```

`REVOKE CREATE ... FROM PUBLIC`는 database 전체 정책 변경이므로 기존 migration role에 필요한
권한을 명시적으로 부여한 뒤 운영자가 실행한다. digest는 secret을 출력하지 않는 다음 명령으로
생성한다.

```powershell
$env:RENAISS_ENV_FILE="$env:ProgramData\Renaiss\secrets\web.env"
.\.venv\Scripts\python.exe -m renaiss_bot.tools.prepare_database --print-target-fingerprint
```

2026년 Telegram의 현재 로그인 계약인 OIDC Authorization Code + PKCE를 사용한다. BotFather의
`Bot Settings > Web Login`에서 발급한 숫자 bot client ID와 secret을 사용한다. client ID는
`RENAISS_EXPECTED_BOT_ID`와 일치해야 한다. `https://tgpoke.com`과 정확한 callback URL을 Allowed
URLs로 등록한다. ID token은 Telegram JWKS의 기본 `RS256` 키, `iss`, `aud`, `iat`, `exp`, `nonce`를
모두 검증한다. BotFather에서 다른 서명 알고리즘을 선택하면 이 서비스는 의도적으로 로그인을
거부한다.

세션 쿠키는 `renaiss_session`, `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/renaiss`다.

## Routes

- `/renaiss`: 공개 도감과 같은 진입 화면
- `/renaiss/pokedex`: 로그인 없이 보는 공개 콜라보 도감
- `/renaiss/mycards`: Telegram 로그인 사용자의 보유 카드만 보는 개인 도감
- `/renaiss/login`: Telegram OIDC 로그인 화면
- `/renaiss/leaderboard`: 매주 KST에 초기화되는 공개 포획 당첨 순위
- `/renaiss/guide`: 실제 봇 명령어와 짧은 게임 가이드
- `/renaiss/api/collection`: 공개 카탈로그와 로그인 사용자의 보유 여부
- `/renaiss/api/leaderboard`: 매주 KST에 초기화되는 익명 공개 포획 당첨 순위
- `/renaiss/api/auth/telegram/start`: OIDC 시작
- `/renaiss/api/auth/telegram/callback`: 등록된 OIDC callback
- `/renaiss/livez`: 프로세스 liveness
- `/renaiss/readyz`: DB, 최소 카탈로그, 인증 설정 readiness

화면은 기존 TGPoke 도감의 밝은 회청색 배경, 884px 흰색 shell, 상단 TGPoke 메뉴,
Telegram 파란 로그인 버튼, 5열/모바일 2열 카드 타일을 재사용한다. 기존 TGPoke의 legacy
Telegram widget·localStorage 세션·홀로그램 glow는 가져오지 않고, 이 서비스의 OIDC와
HttpOnly cookie를 유지한다.

API는 FMV, 포트폴리오 총액 또는 매매 정보를 반환하지 않는다. 리더보드도 누적 도감이나
자산 대신 그 주의 공개 포획 당첨만 집계한다. 가격 공개와 Daily Pick은 Telegram 핵심
루프에서 별도로 다룬다.

## TGPoke path routing

`cloudflared`는 규칙을 위에서 아래로 평가하고 경로 접두사를 제거하지 않는다. 실제 tunnel
설정에는 `ops/cloudflared.tgpoke.ingress.example.yml`의 `/renaiss` 규칙을 기존 TGPoke 전체
규칙보다 먼저 병합한다. 저장소의 예시는 credential 파일을 포함하지 않는다.

같은 hostname의 경로 분리는 browser origin을 격리하지 않는다. 따라서 현재 요구사항인
`tgpoke.com/renaiss`를 쓰는 동안 TGPoke 본체와 이 앱은 하나의 보안 신뢰 경계다. 더 강한
격리가 필요하면 `renaiss.tgpoke.com`에서 서비스하고 `/renaiss`는 그쪽으로 redirect한다.

## Windows service handoff

1. 운영자가 `%ProgramData%\Renaiss\secrets\web.env`와 `%ProgramData%\Renaiss\logs`를
   미리 만들고 전용 서비스 계정에 각각 읽기, 쓰기 최소 권한만 준다.
2. Task Scheduler의 프로그램을 `renaiss_bot\start_renaiss_web.bat`로, 시작 위치를 release
   root로 지정한다. 이 저장소는 task를 자동 등록하지 않는다.
3. Cloudflare 설정을 바꾸기 전에 `http://127.0.0.1:18082/renaiss/readyz`가 HTTP 200인지
   확인한다.
4. `cloudflared tunnel ingress validate`와
   `cloudflared tunnel ingress rule https://tgpoke.com/renaiss`로 실제 설정의 우선순위를
   확인한 뒤 tunnel을 재시작한다.
5. 장애 시 `/renaiss` ingress 규칙과 TGPoke 메뉴 링크를 되돌린다. Cloudflare Tunnel은
   첫 규칙의 origin이 죽어도 다음 규칙으로 자동 fallback하지 않는다.
