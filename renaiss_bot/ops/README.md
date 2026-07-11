# Windows Telegram 파일럿 배치 기준

이 문서는 **Renaiss Telegram 코어 프로세스 하나**를 Windows Task Scheduler로
운영하는 제한된 파일럿 기준이다. Discord와 click tracker는 이 배치 범위에 포함하지
않는다. 이 저장소의 파일을 만든 것만으로 작업이 등록되거나 프로세스가 시작되지는
않는다.

Task Scheduler 등록 스크립트는 제공하지 않는다. 전용 계정의 암호를 PowerShell
인자·명령 기록·CI 로그로 전달하면 안 되고, task별 환경 블록도 Task Scheduler가
안전하게 제공하지 않기 때문이다. gMSA 또는 조직의 credential broker가 확정되기
전에는 Windows UI에서 암호를 직접 입력하고 아래 값을 두 사람이 대조하는 절차가 더
안전하다.

## 파일럿 한계

- Task Scheduler 자체는 Telegram polling의 정지 상태를 확인하지 않는다. 프로세스의
  in-process watchdog이 event loop 장기 정지를 exit 75로 바꾸며, 아래 optional
  loopback health는 별도 로컬 monitor와 운영자 알림에 사용한다.
- launcher 로그는 서비스별 약 60 MiB 기본 상한으로 회전한다. 이 상한과 다른 서비스
  로그를 포함한 볼륨 여유 공간에는 별도 디스크 경보가 필요하다.
- `Do not start a new instance`는 같은 task의 중복만 막는다. 다른 task, 수동 실행,
  다른 호스트 중 같은 DB/bot ID는 PostgreSQL session lock이 막는다. 다른 DB를 쓰는
  기존 TGPOKE/Renaiss poller는 볼 수 없으므로 토큰 기준으로 별도 inventory해야 한다.
- Task Scheduler의 강제 종료 설정만으로 `cmd.exe` → Python → Playwright/Chromium
  전체 프로세스 트리의 종료가 보장됐다고 판단하지 않는다. 파일럿 전 stop rehearsal에서
  실제 parent/child PID, 실행 경로, 서비스 계정을 기록하고 모든 descendant가 사라지는지
  확인한다. 보장할 수 없으면 kill-on-close Job Object 또는 프로세스 트리를 관리하는 전용
  Windows 서비스 관리자를 사용한다.
- in-process watchdog과 session probe는 호스트가 깨어 있다는 전제다. 파일럿 호스트의
  sleep/hibernate를 승인된 전원 정책에서 비활성화하고 실제 적용 상태를 확인한다.

이 제한들을 받아들일 수 없는 상시 서비스는 Task Scheduler가 아니라 전용 Windows
서비스 관리자와 외부 heartbeat를 먼저 준비한다.

## 권장 디렉터리와 권한

관리자 개인 Desktop이나 Git working tree에서 운영하지 않는다. 예시는 다음과 같다.

```text
C:\ProgramData\Renaiss\
├─ app\releases\<release-id>\       application + release-local .venv
├─ secrets\telegram.env             canonical external dotenv
├─ logs\                            launcher stdout/stderr
├─ state\                           operator/runtime state
├─ playwright-cache\                Playwright browser/cache files
└─ temp\                            Chromium temporary profiles
```

`<release-id>`는 commit SHA 또는 변경 불가능한 build ID를 포함한다. `current` junction을
실행 중에 바꾸지 말고 Task action이 정확한 versioned release를 가리키게 한다.

권한은 상속을 검토한 뒤 아래 최소치로 고정한다.

| 경로 | 전용 서비스 계정 | Administrators / SYSTEM | 금지 사항 |
|---|---|---|---|
| `app\releases\<release-id>` 전체 | Read & Execute | Full Control | 서비스 계정의 Write/Modify/Delete |
| `secrets` 디렉터리 | traverse에 필요한 Read & Execute | Full Control | 일반 Users/Authenticated Users 접근 |
| `secrets\telegram.env` | Read | Full Control | 서비스 계정의 Write, 로그/백업으로 복사 |
| `logs` | Modify | Full Control | 소스 트리 안에 배치, 무제한 보존 |
| `state` | Modify | Full Control | 실행 파일이나 task wrapper 저장 |
| `playwright-cache` | Read & Execute | Full Control | 런타임 설치·수정, 다른 서비스 계정과 공유 |
| `temp` | Modify | Full Control | 관리자 개인 TEMP와 공유 |

서비스 계정은 전용 비관리자 계정이어야 한다. `Administrators`, `Remote Desktop
Users` 등에 넣지 않고 `Log on as a batch job`만 허용한다. 대화형 로그인과 RDP는
허용하지 않는다. 앱과 `.venv`의 dependency 설치는 배포 계정이 완료한 다음 ACL을
Read & Execute로 잠근다.

ACL 변경은 계정 SID와 복구용 관리자 접근을 먼저 확인한 뒤 조직의 승인된 도구로
수행한다. 적용 후 다음 read-only 명령의 출력에서 의도하지 않은 writable principal이
없는지 확인한다.

```powershell
icacls "C:\ProgramData\Renaiss\app\releases\<release-id>"
icacls "C:\ProgramData\Renaiss\secrets\telegram.env"
icacls "C:\ProgramData\Renaiss\logs"
icacls "C:\ProgramData\Renaiss\state"
icacls "C:\ProgramData\Renaiss\playwright-cache"
```

## 동일 runtime 계약

준비, preflight, Task action은 모두 해당 release의 같은 interpreter를 사용한다.

```text
C:\ProgramData\Renaiss\app\releases\<release-id>\.venv\Scripts\python.exe
```

관리자 전역 Python이나 인접한 `pokemon-bot`의 venv를 사용하지 않는다. Task process가
시작되기 전에 다음 절대 경로가 보이도록 운영 환경을 구성한다.

```text
RENAISS_ENV_FILE=C:\ProgramData\Renaiss\secrets\telegram.env
RENAISS_LOG_DIR=C:\ProgramData\Renaiss\logs
RENAISS_LOG_MAX_BYTES=10485760
RENAISS_LOG_BACKUPS=5
PLAYWRIGHT_BROWSERS_PATH=C:\ProgramData\Renaiss\playwright-cache
TEMP=C:\ProgramData\Renaiss\temp
TMP=C:\ProgramData\Renaiss\temp
```

`RENAISS_LOG_DIR`, `RENAISS_LOG_MAX_BYTES`, `RENAISS_LOG_BACKUPS`는 Python이
dotenv를 읽기 전에 launcher가 사용하므로 `telegram.env`에 넣어서는 안 된다.
Task Scheduler에는 task별 environment 입력란이
없으므로 위 경로를 inline `cmd /c set ...` 문자열이나 암호 포함 wrapper로 주입하지
않는다. 파일럿 호스트의 machine/service-account 환경을 승인된 방식으로 설정하고,
환경 변경 뒤 새로 생성한 서비스 계정 프로세스에서 값을 read-only로 확인한다.
환경을 신뢰성 있게 주입할 수 없으면 task를 등록하지 않는다.

dotenv는 이미 존재하는 process 환경값을 덮어쓰지 않는다. 따라서 machine/user
환경에는 token, `DATABASE_URL`, API secret, feature flag를 두지 않고 외부 secret
파일에만 둔다. task 등록 전에 값이 아니라 **키 이름만** 조회해 예상하지 않은 ambient
override가 없는지 확인한다. `RENAISS_PYTHON_EXE`도 특별한 이유가 없으면 unset하여
launcher가 release-local `.venv`를 선택하게 한다.

```powershell
Get-ChildItem Env: |
  Where-Object Name -Match '^(RENAISS_|DATABASE_URL$|POKARD_)' |
  Select-Object -ExpandProperty Name
```

Task Scheduler service가 이전 environment block을 보유할 수 있으므로 machine/user
환경 변경 뒤에는 승인된 service restart 또는 host reboot를 거친 새 task process에서
다시 검증한다. 기존에 떠 있던 `cmd.exe`나 Task Scheduler UI의 환경을 근거로 삼지
않는다.

`telegram.env`는 `.env.example`을 기준으로 만들되 최소한 다음 조건을 만족해야 한다.

- 전용 `RENAISS_BOT_TOKEN`, 일치하는 `RENAISS_EXPECTED_BOT_ID`
- 실제 supergroup인 `RENAISS_OFFICIAL_CHAT_ID`
- 운영 PostgreSQL `DATABASE_URL`
- `DATABASE_URL`과 같은 PostgreSQL cluster 및 database를 가리키는 direct/session-mode
  `RENAISS_TELEGRAM_LOCK_DATABASE_URL` — PgBouncer transaction mode 금지
- `RENAISS_SKIP_DB=0`, `RENAISS_API_MOCK_JSON` 미설정
- Daily Pick을 아직 승인하지 않았다면 `RENAISS_DAILY_PICK_ENABLED=0`

## Telegram 로컬 health

health listener는 기본 비활성화다. 파일럿에서 로컬 monitor를 실제 구성할 때만
`telegram.env`에 다음 값을 명시한다.

```text
RENAISS_TELEGRAM_HEALTH_ENABLED=1
RENAISS_TELEGRAM_HEALTH_PORT=18081
```

host 설정은 제공하지 않으며 코드는 IPv4 loopback `127.0.0.1`에만 bind한다. reverse
proxy, portproxy, 외부 firewall publish 대상으로 사용하지 않는다. endpoint는 Telegram
메시지를 보내거나 DB/API를 조회하지 않는다.

- `GET /livez`: event loop가 로컬 HTTP 요청을 처리하면 200이다.
- `GET /readyz`: DB·official chat·job 등록·spawn recovery가 끝나고 PTB Application과
  polling updater가 실행 중이며 단일-poller DB session lock이 유효할 때만 200이다.
  startup/shutdown 또는 lock session 상실 후에는 503이다.

```powershell
Invoke-RestMethod "http://127.0.0.1:18081/livez"
Invoke-WebRequest "http://127.0.0.1:18081/readyz" -UseBasicParsing
```

listener는 PTB `post_init` 초기에 열리므로 DB/chat/recovery 중에는 live와 not-ready를
구분한다. Python import나 PTB의 최초 Telegram initialization보다 앞서 열리지는 않으므로
socket 자체가 없거나 응답하지 않는 상태도 process failure로 처리해야 한다. shutdown에서는
readiness를 먼저 닫고 socket을 정리한다.

단일-poller lock은 전용 PostgreSQL session에 묶여 프로세스 crash/종료 시 DB가 자동
해제한다. 10초 session probe 실패는 graceful stop과 최종 exit 75로 이어진다. 별도
watchdog thread는 event loop heartbeat가 45초 멈추면 graceful stop을 요청하고 60초에
hard exit 75로 전환한다. 따라서 아래 1분 간격의 제한된 실패 재시작 정책을 유지한다.

## 변경 불가능한 release 준비

1. clean commit에서 테스트와 PostgreSQL integration gate를 통과시킨다.
2. versioned release 디렉터리에 소스를 복사하고 그 안에 `.venv`를 만든다.
3. 그 release의 interpreter로 dependency와 Playwright browser를 설치한다.
4. secrets, logs, state, Playwright cache, temp를 release 밖에 만든다.
5. 전용 계정으로 필요한 경로를 읽고 쓸 수 있는지 확인한다.
6. app과 `.venv`를 전용 계정 Read & Execute로 잠근 뒤 파일 hash/commit을 기록한다.

배포 뒤 `.py`, `.bat`, `.venv`를 제자리 수정하지 않는다. 변경은 새 release ID로
다시 만들고 Task action을 승인 후 전환한다.

## prepare → preflight → start

아래 순서는 maintenance window에서 수행한다. `<release>`는 한 번 정한 절대경로를
그대로 사용하며 각 명령의 interpreter가 같은지 다시 확인한다.

1. DB 백업의 생성 시각과 실제 복구 절차를 확인한다.
2. 기존 Telegram task를 disable/stop하고, 같은 토큰을 쓰는 다른 host·task·수동
   Python 프로세스가 없음을 확인한다.
3. 새 release의 명시적 schema preparation을 한 번 실행한다.

```powershell
& "<release>\.venv\Scripts\python.exe" -m renaiss_bot.tools.prepare_database --apply
```

4. 같은 interpreter와 같은 `RENAISS_ENV_FILE`로 read-only DB/Telegram preflight를
   실행한다. 이 단계는 메시지를 보내지 않는다. DB 검사는 양쪽 모두 transaction-scoped
   lock만 사용해 `DATABASE_URL` 쪽 contender 차단·재획득을 확인하고, 두 DSN이 같은
   advisory-lock domain을 공유하는지 smoke-test한다. 외부 endpoint가 direct/session
   mode라는 운영자 증거는 별도다.

```powershell
& "<release>\.venv\Scripts\python.exe" -m renaiss_bot.tools.preflight `
  --check-telegram-config `
  --check-telegram-live
```

5. 모든 결과가 `PASS`이고 official group과 bot ID를 운영자가 다시 대조한 뒤에만
   Task를 enable/start한다.
6. 시작 로그, Task History, DB 연결을 확인한 다음 일반 멤버 한 명이 official room에서
   plain `c`를 보내는 수동 E2E를 한다. 이 E2E만 실제 그룹 메시지를 사용한다.

Daily Pick을 여는 release는 별도로 승인된 exact Partner fixture와 known-good tuple이
필요하다. 그 승인이 없다면 collection pilot으로 유지하고 `--require-daily-pick`이나
live Partner API 설정을 임의로 추가하지 않는다.

## Task Scheduler 수동 등록 체크리스트

등록 전 Task XML/GUI 화면을 두 사람이 함께 검토한다.

### General

- 이름: `Renaiss\TelegramPilot`
- 사용자: 위 전용 비관리자 계정 하나
- `Run whether user is logged on or not`: 켬
- `Run with highest privileges`: **끔**
- 암호는 Windows credential prompt에만 입력하고 문서·스크립트·환경에 저장하지 않음

### Trigger

- `At startup` 하나, 30~60초 delay
- 같은 실행 파일을 반복 호출하는 별도 minute trigger 없음

### Action

- Program: `<release>\.venv\Scripts\python.exe`
- Arguments:
  `-B -E -s -u -m renaiss_bot.tools.stream_runner --source-root "<release>" --log-path "<external-log-dir>\renaiss_bot_service.log" --max-bytes 10485760 --backups 5 --module renaiss_bot.main`
- Start in: `<release>` 절대경로
- action과 Start in에 Desktop, relative path, `pokemon-bot` 경로가 없음

Task가 Python을 직접 소유해야 stop 시 stream runner와 그 자식이 함께 종료된다.
`cmd.exe` → batch → Python 액션은 Task가 `Ready`로 돌아간 뒤 Python poller가 고아로
남을 수 있으므로 사용하지 않는다. release-local `.venv`와 외부 log directory가
선택됐는지 최초 시작 로그로 확인한다.

### Conditions / Settings

- `Start the task only if the computer is idle`: 끔
- `Stop if the computer switches to battery power`: 서버 정책에 맞게 끔
- 호스트 sleep/hibernate: 승인된 서버 전원 정책에서 비활성화하고 적용 상태를 확인
- `Allow task to be run on demand`: 켬
- `Run task as soon as possible after a scheduled start is missed`: 켬
- `If the task fails, restart every`: 1분
- `Attempt to restart up to`: 5회
- `Stop the task if it runs longer than`: 끔
- `If the running task does not end when requested, force it to stop`: 켬
- `If the task is already running`: **Do not start a new instance**

`force it to stop`을 켠 것만으로 종료 검증을 대신하지 않는다. maintenance rehearsal에서
`cmd.exe`, release-local Python, 그 Python이 만든 Playwright/Chromium의 parent/child PID를
기록하고 stop 뒤 모두 종료되는지 확인한다. descendant가 남는 환경에서는 새 인스턴스를
시작하지 말고 kill-on-close Job Object 또는 process-tree 수명주기를 보장하는 서비스
관리자로 전환한다.

5회 실패 후에는 자동 반복을 늘리지 않는다. task를 disable하고 외부 log, Task History,
동일 release의 preflight를 확인한다. exit code 2는 대개 환경/권한 설정 오류이므로
재시작으로 해결하지 않는다.

등록 뒤 다음 read-only 명령으로 principal, action, state, last result를 대조한다.

```powershell
Get-ScheduledTask -TaskPath "\Renaiss\" -TaskName "TelegramPilot" |
  Select-Object TaskName, State, Principal, Actions, Settings
Get-ScheduledTaskInfo -TaskPath "\Renaiss\" -TaskName "TelegramPilot"
```

## 전환, rollback, 제거

### 새 release 전환

1. 새 release에서 prepare와 preflight를 끝낸다.
2. 기존 task를 disable/stop한다.
3. 실제 child Python과 그 Playwright/Chromium descendant가 종료됐는지 **parent PID,
   경로, 서비스 계정으로** 확인한다. 이름만 보고 모든 `python.exe`나 Chromium을 종료하지
   않으며, descendant가 남아 있으면 새 release를 시작하지 않는다.
4. Task action과 Start in을 새 versioned release로 바꾸고 다시 두 사람이 검토한다.
5. enable/start 후 동일한 smoke/E2E를 반복한다.

### 코드 rollback

1. 현재 task를 disable/stop한다.
2. 보존한 이전 immutable release가 현재 additive schema와 호환되는지 staging 결과를
   확인한다.
3. Task action을 이전 release의 launcher로 되돌린다.
4. 이전 release의 같은 interpreter/env로 read-only preflight를 다시 통과시킨 뒤
   start한다.

rollback을 위해 테이블이나 컬럼을 DROP하지 않는다. DB restore가 필요한 데이터 손상은
일반 코드 rollback과 분리된 incident 절차와 승인된 백업으로만 처리한다.

### Task 제거

1. task를 disable/stop하고 Python 및 Playwright/Chromium descendant 종료를 parent PID,
   경로, 서비스 계정으로 확인한다.
2. Task Scheduler에서 설정을 export해 incident/change 기록에 보관한다.
3. GUI에서 task를 삭제하거나, 운영자가 이름을 다시 확인한 뒤 confirmation이 있는
   `Unregister-ScheduledTask`를 사용한다.
4. logs/state/secrets/이전 release는 즉시 삭제하지 않고 보존 정책과 참조 task를
   확인한다.
5. 서비스 폐기 또는 계정/호스트 침해라면 Telegram token과 DB/API secret을
   rotation한다.

## 배포 허용 판정

다음 중 하나라도 만족하지 않으면 파일럿 task를 시작하지 않는다.

- 전용 비관리자 계정과 위 ACL matrix가 검증됨
- release 소스/venv가 service account에 Read & Execute only임
- secret은 Read only, Playwright cache는 Read & Execute only이며 logs/state/temp는
  release 밖 Modify임
- 동일 interpreter/env의 prepare와 read-only preflight가 PASS임
- lock DSN이 `DATABASE_URL`과 같은 PostgreSQL cluster/database의 direct 또는
  session-mode endpoint임이 확인됨(transaction pooling 금지)
- 같은 bot token을 쓰는 다른 poller가 없음
- single-instance와 bounded restart 설정이 UI/조회 결과로 확인됨
- 호스트 sleep/hibernate가 비활성화되고, stop rehearsal에서 전체 child process 종료가
  확인됐거나 Job Object/서비스 관리자가 process-tree 수명주기를 보장함
- rollback release, DB backup, task 제거 책임자가 지정됨
