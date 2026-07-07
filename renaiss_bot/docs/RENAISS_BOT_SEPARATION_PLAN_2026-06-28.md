# Renaiss Bot Separation Plan

작성일: 2026-06-28 KST  
목적: 기존 TGPOKE/푸키먼 본체와 분리된 Renaiss 전용 텔레그램 봇을 준비하기 위한 실행 계획

---

## 1. 결론

Renaiss 해커톤/제휴용 제품은 기존 TGPOKE 봇에 기능을 계속 얹기보다, **Renaiss 전용 봇으로 분리하는 방향**이 더 좋다.

추가 결정: 이 봇은 포켓몬 전용으로 잠그지 않는다.  
P0는 TGPOKE 엔진을 활용해 포켓몬 TCG 카드팩으로 시작하지만, Renaiss API가 카드 메타데이터와 가격을 제공하는 경우 **원피스 카드, 스포츠 카드, 기타 TCG/수집 카드도 같은 구조로 확장**한다.

단, 처음부터 레포와 DB를 완전히 분리하지 않는다.  
1차 구현은 같은 코드베이스 안에 `renaiss_bot/` 독립 앱 폴더를 만들고, 카드팩 엔진/렌더러/카드 DB 연결 지점만 공유 가능하게 둔다.

최종 구조:

```text
TGPOKE Bot
  - 기존 푸키먼 게임
  - 도감, IV, 업적, 팀세팅, 토너먼트
  - 기존 BOT_TOKEN 사용

Renaiss Bot
  - 별도 텔레그램 봇 토큰
  - P1/P2 Discord 봇 토큰 추가 가능
  - Renaiss 브랜딩
  - 포켓몬 TCG 카드팩 개봉
  - P1부터 원피스/기타 Renaiss 지원 카드팩 확장
  - Renaiss FMV 상단바
  - Renaiss 가격 보기 금색 CTA
  - 내 레퍼럴 링크
  - 가격 링크 클릭 KPI

Shared Core
  - 카드팩 뽑기 로직
  - 카드 메타데이터
  - 브랜드/게임별 카드 스키마 어댑터
  - 카드 이미지 렌더링
  - Renaiss 가격 API 클라이언트
  - 레퍼럴 URL 빌더

Platform Adapters
  - Telegram adapter: slash/text commands, inline keyboard, photo reply
  - Discord adapter: slash commands, embeds, buttons, image attachment
```

---

## 2. 왜 분리하는가

### 2.1 TGPOKE 본체 보호

TGPOKE는 이미 시즌2 운영, 카드팩, 도감, IV, 업적, 팀세팅, 토너먼트가 붙어 있다.  
Renaiss 실험을 여기에 직접 얹으면 운영 중인 봇의 안정성을 해칠 수 있다.

분리하면:

- TGPOKE 장애 없이 Renaiss 실험 가능
- Renaiss API 장애가 TGPOKE 전투/도감/팀세팅에 영향 없음
- 해커톤 데모 중 기존 유저 경험을 망치지 않음

### 2.2 제출물 메시지 명확화

해커톤 제출물은 다음 한 문장으로 설명할 수 있어야 한다.

```text
Renaiss Bot is a Telegram TCG pack-opening bot powered by TGPOKE's pack engine and enhanced with Renaiss FMV links, starting with Pokémon cards and expandable to One Piece and other Renaiss-supported collectibles.
```

한국어:

```text
Renaiss Bot은 TGPOKE 카드팩 엔진을 활용한 텔레그램 TCG 팩오픈 봇이다.
P0는 포켓몬 카드로 시작하고, Renaiss API가 제공하는 경우 원피스/기타 카드도 같은 방식으로 붙인다.
희귀 카드에는 Renaiss 가격/FMV/레퍼럴 링크를 붙인다.
```

### 2.3 레퍼럴과 KPI 분리

Renaiss 전용 봇이면 다음 KPI를 깔끔하게 추적할 수 있다.

- Renaiss 봇 유입 유저
- 카드팩 개봉 수
- 대표 카드 FMV 노출 수
- Renaiss 가격 버튼 클릭 수
- 레퍼럴 링크 클릭률
- 카드별 가격 클릭률
- API 매칭 성공률

---

## 3. 제품 이름

추천 이름:

```text
TGPOKE Renaiss Edition
```

외부 설명용:

```text
A Telegram Pokémon TCG pack-opening bot with Renaiss FMV overlays and referral-linked card pages.
```

봇 내부 노출:

```text
푸키먼 Renaiss Edition
포켓몬 TCG 카드팩을 열고 Renaiss 기준가를 확인하세요.
```

확장형 외부 설명:

```text
A Telegram TCG pack-opening bot for Renaiss-supported cards, starting with Pokémon and expanding to One Piece and other collectible card categories as Renaiss data becomes available.
```

---

## 4. 사용자 경험

### 4.1 기본 흐름

```text
/start
  -> Renaiss Edition 안내
  -> 무료 데모팩 1개 지급
  -> 지원 카드 카테고리 표시

/open
  -> 카드팩 개봉
  -> 대표 카드 1장 선정
  -> Renaiss API로 FMV 조회
  -> 이미지 상단에 Renaiss FMV 바 렌더링
  -> 채팅 텍스트에 7일 가격변화 표시
  -> 금색 "Renaiss에서 가격 보기" 버튼 제공

/mycards
  -> 내 카드 요약
  -> 내 컬렉션 가치
  -> 최근 최고가 카드

/price 리자몽
  -> 카드 후보 검색
  -> Renaiss 링크 제공

/sets
  -> 지원 카드팩 카테고리 표시
  -> Pokémon / One Piece / 기타 Renaiss 지원 카드
```

### 4.2 카드팩 결과 메시지

```text
🎴 Renaiss Edition 팩 개봉 완료!

🔥 최고 카드: 리자몽 ex SAR
💰 Renaiss 기준가: $430
📈 7일 가격변화: +12.4%
📚 내 수집률: 42% -> 44%

[Renaiss에서 가격 보기]
[내 컬렉션 보기]
```

### 4.3 이미지 상단바

이미지에는 가격변화까지 넣지 않는다.  
상단바는 복잡하지 않게 유지한다.

```text
┌────────────────┬──────────────┬──────────────┬────────────────┐
│ RENAISS        │ GRADE        │ MARKET       │ FMV            │
│ TCG MARKET     │ PSA 10       │ FMV LIVE     │ $430           │
└────────────────┴──────────────┴──────────────┴────────────────┘
```

최근 가격변화는 채팅 텍스트에 둔다.

---

## 5. 명령어 범위

### P0 명령어

| 명령어 | 기능 |
|---|---|
| `/start` | 봇 소개, 무료팩 지급, 주요 버튼 |
| `/open` | 보유 팩 1개 개봉 |
| `/pack` | 현재 보유 팩 확인 |
| `/mycards` | 내 카드/컬렉션 요약 |
| `/price <card>` | Renaiss 가격/검색 링크 확인 |
| `/sets` | 지원 카드 카테고리 확인 |

### P1 명령어

| 명령어 | 기능 |
|---|---|
| `/drop` | 운영자 라이브 드롭 생성 |
| `/claim` | 드롭 참여 |
| `/leaderboard` | 최고가 pull / 클릭 랭킹 |
| `/daily` | 오늘의 무료팩 |
| `/set <category>` | 기본 카드팩 카테고리 선택 |

### 한국어 alias

| 한국어 | 매핑 |
|---|---|
| 카드오픈 | `/open` |
| 팩열기 | `/open` |
| 내카드 | `/mycards` |
| 가격 | `/price` |
| 세트 | `/sets` |

---

## 5.1 Discord 버전

Discord 버전도 구현 가능하다.  
단, TGPOKE/Renaiss 게임 로직을 다시 만들지 않고, 같은 `renaiss_bot/services` core를 호출하는 **플랫폼 어댑터**로 만든다.

### Discord UX

```text
/open
  -> ephemeral 또는 channel 응답
  -> 카드팩 결과 embed
  -> 대표 카드 이미지 attachment
  -> Renaiss FMV 표시
  -> 7일 가격변화는 embed description에 표시
  -> 금색 CTA에 해당하는 "Renaiss에서 가격 보기" button

/sets
  -> Pokémon / One Piece / 기타 지원 카테고리 표시

/price card:리자몽
  -> Renaiss 후보/가격 검색
```

### Telegram과 Discord 차이

| 항목 | Telegram | Discord |
|---|---|---|
| 명령 | `/open`, 텍스트 alias | slash command |
| 결과 | 메시지 + photo + inline button | embed + image attachment + button |
| 개인 응답 | DM 또는 채팅방 | ephemeral response 가능 |
| 커뮤니티 이벤트 | `/drop`, `/claim` | `/drop`, button claim |
| 가격 버튼 | InlineKeyboardButton URL | Discord Button URL |

### Discord 파일 구조

```text
renaiss_bot/
  main.py
  adapters/
    telegram/
      commands.py
      views.py
    discord/
      main.py
      commands.py
      embeds.py
      views.py
```

### 환경변수

```text
RENAISS_BOT_TOKEN=...
RENAISS_DISCORD_TOKEN=...
RENAISS_DISCORD_APP_ID=...
RENAISS_DISCORD_GUILD_ID=...  # 개발/데모 서버용
```

### 구현 우선순위

P0에서는 Telegram 봇을 먼저 만든다.  
Discord는 P1 또는 P2로 둔다.

이유:

- 해커톤 데모는 Telegram pack opening이 더 빠르게 보인다.
- 현재 TGPOKE 코드가 Telegram 기반이라 P0 구현 속도가 빠르다.
- Discord는 slash command 등록, guild 권한, embed/view 구조가 별도로 필요하다.

다만 core를 처음부터 플랫폼 독립으로 만들면 Discord 확장은 크지 않다.

```text
open_pack(user_id, category)
  -> PackOpenResult

build_renaiss_overlay(best_card, price)
  -> image bytes

build_renaiss_url(asset, referral)
  -> url
```

Telegram과 Discord는 이 결과를 각각 자기 메시지 포맷으로 보여주기만 한다.

---

## 6. 코드 구조

현재 구조:

```text
main.py
  - BOT_TOKEN
  - register_all_handlers(app)
  - register_all_jobs(app)
  - TGPOKE 전체 기능 실행
```

추가할 구조:

```text
renaiss_bot/
  main.py
  README.md
  handlers/
    __init__.py
    register.py
    start.py
    cardpack.py
    price.py
    callbacks.py
  services/
    __init__.py
    client.py
    matcher.py
    pricing.py
    referral.py
    kpi.py
    captions.py
    categories.py
    pack.py
  database/
    connection.py
    schema.py
    queries.py
  renderers/
    overlay.py
  adapters/              # P1/P2
    discord/
      commands.py
      embeds.py
      views.py
```

### 6.1 `renaiss_bot/main.py`

역할:

- `RENAISS_BOT_TOKEN` 사용
- TGPOKE 전체 핸들러를 등록하지 않음
- Renaiss 전용 핸들러만 등록
- 카드팩/렌더러 shutdown hook은 공유

의사 코드:

```python
def main():
    token = os.getenv("RENAISS_BOT_TOKEN")
    app = (
        Application.builder()
        .token(token)
        .post_init(post_init_renaiss)
        .post_shutdown(post_shutdown_renaiss)
        .concurrent_updates(True)
        .build()
    )
    register_handlers(app)
    app.run_polling(drop_pending_updates=True)
```

### 6.2 `renaiss_bot/handlers/cardpack.py`

역할:

- `/open`, `카드오픈`, `팩열기`
- 기존 `services.season2.cardpack`의 팩 개봉 함수를 호출
- 대표 카드 1장만 Renaiss 가격 조회
- Renaiss 상단바 이미지와 텍스트 메시지 전송
- 금색 CTA 버튼 제공

중요:

TGPOKE의 `handlers.season2.cardpack.py`를 통째로 import해서 쓰지 않는다.  
그 파일은 TGPOKE 운영용 부가 기능이 많으므로, Renaiss 봇은 얇은 핸들러를 새로 만든다.

### 6.3 `renaiss_bot/services/client.py`

역할:

- Renaiss API/SDK 호출
- timeout 관리
- 응답 정규화

기본 정책:

```text
API timeout: 2.5초
대표 카드만 즉시 조회
가격 실패 시 카드팩 결과는 정상 전송
```

### 6.4 `renaiss_bot/services/pricing.py`

역할:

- 캐시 우선 조회
- API 조회
- fallback 상태 결정

상태:

```text
exact
candidate
search_only
missing
api_error
```

### 6.7 `renaiss_bot/services/categories.py`

역할:

- 지원 카드 카테고리 관리
- 포켓몬/원피스/기타 카드별 매칭 필드 정의
- Renaiss API의 category, collection, franchise 값을 로컬 pack type과 연결

초기 카테고리:

```text
pokemon_tcg
one_piece_tcg
other_renaiss_cards
```

P0에서는 `pokemon_tcg`만 실제 팩오픈 대상으로 둔다.  
P1에서 Renaiss API가 원피스 카드 메타데이터와 가격을 안정적으로 제공하면 `one_piece_tcg`를 켠다.

### 6.5 `renaiss_bot/services/referral.py`

역할:

- Renaiss URL에 내 레퍼럴 파라미터 부착
- 정확한 파라미터가 확정되기 전까지 환경변수로 제어

환경변수:

```text
RENAISS_BASE_URL=https://www.renaiss.xyz
RENAISS_REFERRAL_CODE=...
RENAISS_REFERRAL_PARAM=ref
```

### 6.6 `renaiss_bot/services/kpi.py`

역할:

- 가격 버튼 노출 로그
- 클릭 로그
- API 매칭 성공률
- 카드별 클릭률

---

## 7. DB 분리 원칙

초기에는 같은 DB를 쓰되, Renaiss 관련 테이블은 prefix를 분리한다.

```text
renaiss_card_links
renaiss_price_snapshots
renaiss_referral_clicks
renaiss_pack_events
renaiss_user_cards
renaiss_user_preferences
```

TGPOKE의 기존 `user_cards`를 바로 공유할지는 신중해야 한다.

추천:

- 카드 메타데이터는 공유
- 유저 보유 카드와 팩 이벤트는 Renaiss 전용 테이블로 분리
- 유저별 기본 카드 카테고리는 Renaiss 전용 preference로 관리
- Telegram user_id는 같아도 게임 진행 상태는 분리

이유:

- TGPOKE 본체 밸런스와 Renaiss 데모가 섞이지 않는다.
- 해커톤 테스트 유저가 TGPOKE 실제 유저 DB를 오염시키지 않는다.
- 나중에 Renaiss 봇만 떼어내기 쉽다.

---

## 8. Renaiss API 연동 정책

가격 조회는 정확 매칭이 1순위다.

```text
local_card_id
  -> category + card_name + set_code + collector_number + language + grade
  -> Renaiss asset_id
  -> Renaiss FMV / 7d change / asset URL
```

카테고리별 매칭 키:

| 카테고리 | 필수 매칭 키 | 보조 키 |
|---|---|---|
| Pokémon TCG | card_name, set_code, collector_number, language, grade | rarity, release_year |
| One Piece TCG | card_name, set_code, card_number, language, grade | leader/color, rarity |
| 기타 카드 | category, card_name, collection_id, item_number, grade | franchise, year |

카드 브랜드가 달라도 Renaiss 연동 로직은 동일하다.

```text
카드 식별자 -> Renaiss asset_id -> FMV/변동/링크 -> 레퍼럴 CTA
```

실행 정책:

```text
1. 캐시 확인
2. 캐시가 신선하면 즉시 사용
3. 캐시가 없으면 Renaiss API 호출
4. 2.5초 안에 오면 이미지에 FMV 반영
5. 늦거나 실패하면 이미지에는 "Renaiss 확인" 또는 "등록 대기"
6. 채팅에는 fallback 상태 표시
```

캐시 TTL:

```text
FMV 캐시: 6시간
asset link 캐시: 7일
매칭 실패 캐시: 24시간
```

---

## 9. Renaiss 버튼 컬러

Renaiss 이동 버튼은 내부 버튼과 구분한다.

```text
background: #efca68
color: #111513
border: #111513
label: Renaiss에서 가격 보기
```

상태별:

| 상태 | 버튼 |
|---|---|
| exact | Renaiss에서 가격 보기 |
| candidate | Renaiss 후보 보기 |
| search_only | Renaiss에서 검색하기 |
| missing | Renaiss 등록 대기 |

`missing`은 클릭 가능한 CTA가 아니라 disabled 상태가 기본이다.

---

## 10. 구현 단계

### P0: 같은 레포 안에서 별도 봇 실행

목표:

```text
python -m renaiss_bot.main
```

작업:

1. `RENAISS_BOT_TOKEN` 환경변수 추가
2. `renaiss_bot/main.py` 추가
3. `renaiss_bot/handlers/register.py` 추가
4. `/start`, `/open`, `/mycards`, `/price` 최소 구현
5. `/sets`로 지원 카드 카테고리 안내
6. 대표 카드 1장 Renaiss FMV 상단바 렌더링
7. 금색 Renaiss CTA 버튼
8. 레퍼럴 URL 빌더
9. 가격 조회 실패 fallback
10. 클릭 로그 테이블

완료 기준:

- TGPOKE 기존 봇을 끄지 않고 Renaiss 봇을 따로 실행 가능
- Renaiss 봇에서 Pokémon TCG 카드팩 1개 개봉 가능
- 대표 카드 이미지에 Renaiss FMV 상단바 표시
- 채팅 텍스트에 7일 변화 표시
- Renaiss 버튼이 레퍼럴 URL로 연결
- `/sets`에서 Pokémon P0, One Piece P1 확장 예정 상태 표시

### P1: 라이브 드롭과 KPI

작업:

1. `/drop`, `/claim` 추가
2. 라이브 카드팩 드롭 이벤트
3. Renaiss 링크 클릭률 대시보드 API
4. 최고가 pull 랭킹
5. 최근 상승 카드 랭킹
6. One Piece 카드팩 카테고리 추가
7. 카테고리별 최고가 pull 랭킹
8. Discord 데모 서버용 slash command 버전 추가

### P2: 완전 분리 준비

작업:

1. `services/cardpack_core`로 공통 카드팩 엔진 분리
2. TGPOKE와 Renaiss가 공통 core를 사용하도록 정리
3. Renaiss 전용 DB 또는 schema 분리
4. 별도 배포 단위 준비
5. Telegram/Discord adapter를 별도 패키지로 정리

---

## 11. 리스크와 대응

| 리스크 | 대응 |
|---|---|
| Renaiss API 지연 | 2.5초 timeout, 캐시, fallback |
| 카드팩 대량 개봉으로 봇 지연 | 대표 카드 1장만 가격 조회, 렌더링 세마포어 유지 |
| TGPOKE DB 오염 | Renaiss 유저 카드/팩 이벤트 별도 테이블 |
| 레퍼럴 파라미터 미확정 | `RENAISS_REFERRAL_PARAM` 환경변수화 |
| 카드 매칭 실패 | `exact/candidate/search_only/missing` 상태 분리 |
| 버튼이 내부 이동과 헷갈림 | Renaiss 버튼 금색 CTA 고정 |
| 브랜드별 카드 스키마 차이 | category adapter로 매칭 필드 분리 |
| Discord slash command 등록 지연 | 데모 서버 guild command 우선 사용 |
| Discord 권한/초대 설정 누락 | bot invite scope와 channel permission 체크리스트 운영 |

---

## 12. 당장 하지 않을 것

P0에서는 하지 않는다.

- TGPOKE 팀세팅/토너먼트 이식
- IV/업적 성장 전체 이식
- 모든 카드 가격 실시간 조회
- 모든 카드 이미지에 Renaiss 상단바 추가
- 완전 별도 레포 분리
- 실거래/구매 추천 문구
- 원피스/기타 카드 실제 팩 밸런스 완성
- Discord 공개 배포
- Discord 서버별 운영 설정

이유:

P0 목표는 "Renaiss 전용 봇으로 카드팩을 열고, 대표 카드의 Renaiss 가격을 확인하게 만드는 것"이다.

---

## 13. 최종 P0 스펙

```text
Renaiss Bot by TGPOKE

- 별도 텔레그램 봇
- P0: 포켓몬 TCG 카드팩 오픈
- P1: 원피스/기타 Renaiss 지원 카드팩 확장
- 대표 카드 1장 Renaiss FMV 상단바
- 채팅 텍스트에 7일 가격변화
- 금색 Renaiss 가격 보기 버튼
- 내 레퍼럴 링크
- 클릭 로그 KPI
- TGPOKE 본체와 운영 분리
- P1/P2 Discord adapter 확장 가능
```
