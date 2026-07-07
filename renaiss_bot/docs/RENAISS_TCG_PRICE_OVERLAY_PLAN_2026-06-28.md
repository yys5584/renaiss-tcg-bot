# Renaiss TCG Price Overlay Integration Plan

작성일: 2026-06-28 KST  
목적: 텔레그램 TCG 가챠게임의 카드팩 결과 이미지 상단 PSA 영역을 Renaiss 가격/링크 레이어로 교체하는 상세 기획안

---

## 1. 핵심 결론

우리 게임의 시작점은 **포켓몬 TCG 카드팩을 까는 재미**다.  
Renaiss는 이 재미를 대체하는 것이 아니라, 희귀 카드를 뽑았을 때 **실제 시장 기준가와 외부 확인 링크를 붙여주는 신뢰 레이어**로 들어가는 것이 맞다.

최종 확정 방향:

```text
TGPOKE / 푸키먼 = 텔레그램 TCG 가챠게임 엔진
Renaiss = 대표 카드 FMV, 가격변화 텍스트, 외부 가격 확인 링크, 레퍼럴 유입 레이어
```

즉, 유저가 즐기는 게임 루프는 TGPOKE 안에서 유지한다.  
Renaiss는 전투력이나 게임 진행 조건이 아니라, 희귀 카드를 뽑은 순간 "실제 카드 가치 확인"으로 넘어가게 만드는 컬러 CTA와 데이터 레이어다.

카드 카테고리 전략:

```text
P0: Pokémon TCG
P1: One Piece TCG
P1/P2: Renaiss API가 지원하는 기타 TCG/스포츠/수집 카드
```

Renaiss API가 카드 메타데이터, FMV, asset URL을 제공한다면 브랜드가 달라도 같은 구조로 붙인다.

추가 결정:

```text
해커톤/제휴용 P0는 TGPOKE 본체에 직접 붙이지 않고,
같은 코드베이스 안의 별도 Renaiss Bot으로 먼저 구현한다.
```

세부 분리 계획은 `RENAISS_BOT_SEPARATION_PLAN_2026-06-28.md`를 기준으로 한다.

최종 사용자 경험:

```text
텔레그램에서 카드팩 개봉
  -> 카드 이미지 공개
  -> 이미지 상단에 Renaiss 로고, FMV 가격, 등급/시장 정보 표시
  -> 최근 가격변화는 이미지가 아니라 채팅 텍스트/AI 설명에 표시
  -> 금색 Renaiss CTA 버튼 클릭
  -> 내 레퍼럴 링크가 붙은 Renaiss 카드/검색 페이지로 이동
```

이 방식의 장점:

- 텔레그램 유저는 기존처럼 카드팩 재미로 들어온다.
- 희귀 카드가 뜬 순간 실제 카드 가격 확인 욕구가 생긴다.
- Renaiss는 가격/마켓 링크 제공자로 자연스럽게 노출된다.
- 운영자는 `팩 개봉 -> 가격 확인 클릭` 전환율을 KPI로 볼 수 있다.
- 해커톤 제출물로는 Game, Tool, Data integration 성격이 모두 생긴다.

---

## 1.1 Renaiss 버튼 디자인 원칙

Renaiss로 나가는 버튼은 내부 버튼과 색을 다르게 둔다.  
내부 버튼은 검정/회색/파랑 계열로 두고, Renaiss 버튼은 **금색 CTA**로 고정한다.

추천 스타일:

```text
배경: #efca68 또는 #f2cf74
글자: #111513
테두리: #111513
문구: Renaiss에서 가격 보기
```

상태별 버튼:

| 상태 | 버튼 문구 | 버튼 컬러 |
|---|---|---|
| exact | Renaiss에서 가격 보기 | 금색 CTA |
| candidate | Renaiss 후보 보기 | 금색 CTA |
| search_only | Renaiss에서 검색하기 | 금색 CTA |
| missing | Renaiss 등록 대기 | 회색 disabled |

이렇게 해야 유저가 "이 버튼은 게임 내부 이동이 아니라 외부 가격 확인"이라고 바로 인식한다.

---

## 2. 이미지 상단바 디자인 방향

현재 예시 이미지의 상단 PSA 영역은 다음 기능을 한다.

```text
PSA 로고 / 등급 / 세부 점수 / 가격
```

이걸 Renaiss 버전으로 바꾸면 다음처럼 된다.

```text
Renaiss 로고 / 카드 등급 / 시장 신호 / FMV 가격
```

### 2.1 추천 레이아웃

```text
┌────────────────┬──────────────┬──────────────┬────────────────┐
│ RENAISS        │ GRADE        │ MARKET       │ FMV            │
│ TCG MARKET     │ PSA 10       │ LIVE         │ $430           │
└────────────────┴──────────────┴──────────────┴────────────────┘
```

### 2.2 각 영역 의미

| 영역 | 표시값 | 설명 |
|---|---|---|
| Renaiss 브랜드 | RENAISS / TCG MARKET | PSA 로고 대신 Renaiss 로고와 시장 레이어 느낌 |
| Grade | PSA 10, BGS 9.5, Raw 등 | Renaiss 카드 데이터의 등급 정보 |
| Market | LIVE, LISTED, UNLISTED, LOW LIQUIDITY | 시장 상태 요약 |
| FMV | $430 | Renaiss 기준 FMV 또는 reference price |

최근 가격변화는 이미지 상단바에 넣지 않는다. 이미지에는 카드와 가격이 먼저 보여야 하므로, 변동률은 채팅 메시지와 상세 화면에서만 표시한다.

### 2.3 가격 표기 원칙

가격은 게임 재화처럼 보이면 안 된다. 아래처럼 표기한다.

```text
FMV $430
7D +12.4%
via Renaiss
Updated 2026-06-28
```

한국어 화면에서는:

```text
Renaiss 기준가 $430
7일 변동 +12.4%
업데이트 2026-06-28
```

법적 문구를 크게 전면에 깔 필요는 없지만, 상세 화면이나 푸터에는 작게 둔다.

```text
Renaiss 기준가는 게임 내 참고 지표이며 구매 권유가 아닙니다.
```

---

## 3. 텔레그램 카드팩 결과 UX

### 3.1 카드팩 개봉 후 봇 메시지

```text
🎴 카드팩 개봉 완료!

🔥 최고 카드: 리자몽 ex SAR
💰 Renaiss 기준가: $430
📈 7일 가격변화: +12.4%
📈 내 컬렉션 가치: +$430
📚 도감 수집률: 42% -> 44%

[Renaiss에서 가격 보기]
[내 컬렉션 보기]
```

### 3.2 이미지 구성

```text
┌────────────────────────────────────┐
│ RENAISS | GRADE PSA 10 | FMV $430  │
├────────────────────────────────────┤
│                                    │
│        TCG 카드 이미지             │
│                                    │
└────────────────────────────────────┘
```

중요한 점:

- 모든 카드에 Renaiss 가격을 붙일 필요는 없다.
- 카드팩 10장 전체보다 `대표 카드 1~3장`에만 고급 상단바를 붙인다.
- 나머지는 텍스트 요약으로 처리해 렌더링 속도를 지킨다.
- 가격 조회 실패 시 이미지 생성을 막지 않는다.

가격 조회 실패 시:

```text
Renaiss 가격 준비중
링크로 직접 확인
```

---

## 4. 레퍼럴 링크 설계

### 4.1 목표

모든 Renaiss 이동은 내 레퍼럴을 통해 나가야 한다.

예시:

```text
https://www.renaiss.xyz/card/{assetId}?ref={MY_REFERRAL_CODE}
```

또는 Renaiss가 다른 파라미터를 쓰면:

```text
https://www.renaiss.xyz/card/{assetId}?invite={MY_REFERRAL_CODE}
https://www.renaiss.xyz/card/{assetId}?r={MY_REFERRAL_CODE}
```

정확한 파라미터는 Renaiss 코칭 또는 문서에서 확인해야 한다.

### 4.2 구현 원칙

레퍼럴 파라미터를 코드 곳곳에 박지 말고, URL 빌더 하나로 모은다.

```text
build_renaiss_referral_url(base_url, referral_code)
```

환경변수:

```text
RENAISS_REFERRAL_CODE=...
RENAISS_REFERRAL_PARAM=ref
RENAISS_BASE_URL=https://www.renaiss.xyz
```

나중에 `ref`가 아니라 `invite`라면 환경변수만 바꾸면 된다.

### 4.3 클릭 추적

우리는 레퍼럴 클릭 자체도 KPI로 봐야 한다.

```text
user_id
chat_id
pokemon_card_id
renaiss_asset_id
referral_url
clicked_at
source_message_id
```

운영 KPI:

- 카드팩 개봉 수
- Renaiss 링크 노출 수
- Renaiss 링크 클릭 수
- 카드별 클릭률
- 고가 카드 클릭률
- 팩 개봉 후 10분 내 클릭률

---

## 5. Renaiss 가격 데이터 연동

### 5.1 현재 확인한 사실

Renaiss 공식 사이트에는 다음 성격의 데이터가 노출되어 있다.

- Gacha
- Marketplace
- 카드 검색
- Pokémon 카드
- PSA/BGS 등급 카드
- FMV 가격
- Listed/Unlisted 상태
- Buy now / Make an offer 링크

다만 공개 API/SDK 문서는 현재 검색으로 명확히 확인되지 않았다.  
따라서 코칭에서 반드시 물어볼 질문은 하나다.

```text
What SDK, API, or data endpoint should we use for Renaiss card metadata, FMV prices, and asset page links?
```

### 5.2 연동 우선순위

1순위: Renaiss 공식 API/SDK

```text
카드 검색 -> asset id 획득 -> FMV/상태/URL 획득 -> 캐시 저장
```

2순위: Renaiss가 제공하는 export 또는 partner data

```text
CSV/JSON mapping -> 우리 카드 DB와 매칭 -> 가격 스냅샷 저장
```

3순위: 수동 매핑 + Renaiss 검색 링크

```text
카드명/세트/번호로 Renaiss 검색 URL 생성
가격은 "Renaiss에서 확인"으로 표시
```

스크래핑은 최후순위다. 약관과 안정성 문제가 생길 수 있으므로 해커톤 제출물에서는 피하는 편이 낫다.

---

## 6. 카드 매칭 로직

카드는 이름만으로 매칭하면 오매칭이 많다.  
다음 순서로 매칭해야 한다.

### 6.1 정확 매칭

```text
category
card_name
set_code
collector_number
language
rarity
grade
```

예:

```text
Charizard ex
SV4a
349/190
Japanese
SAR
PSA 10
```

원피스 예시:

```text
one_piece_tcg
Monkey.D.Luffy
OP05
OP05-119
Japanese
SEC
PSA 10
```

### 6.2 후보 매칭

정확 매칭 실패 시:

```text
category + card_name + set_name
category + card_name + collector_number
category + card_name + rarity
```

후보가 여러 개면 관리자 페이지에서 1회 선택해 저장한다.

### 6.3 매칭 실패

```text
Renaiss 가격 준비중
Renaiss에서 직접 검색
```

이때도 레퍼럴 링크는 검색 URL에 붙일 수 있다.

---

## 7. 필요한 DB 테이블

### 7.1 `renaiss_card_links`

```text
id
local_card_id
category
card_name
set_code
collector_number
language
rarity
grade
renaiss_asset_id
renaiss_url
referral_url
match_status
match_confidence
created_at
updated_at
```

### 7.2 `renaiss_price_snapshots`

```text
id
local_card_id
renaiss_asset_id
fmv_usd
listed_price_usd
last_sale_usd
change_24h_pct
change_7d_pct
change_30d_pct
market_status
source
price_updated_at
created_at
```

### 7.3 `renaiss_referral_clicks`

```text
id
user_id
chat_id
local_card_id
renaiss_asset_id
source
source_message_id
clicked_at
```

---

## 8. 게임 밸런스 반영

Renaiss 가격은 전투력에 직접 반영하지 않는 것이 좋다.

이유:

- 고가 카드만 절대적으로 강해질 수 있다.
- 실시간 가격 변동이 게임 밸런스를 흔든다.
- 가격 API 장애가 전투 시스템 장애로 이어질 수 있다.

대신 다음에 반영한다.

| 항목 | 반영 여부 |
|---|---|
| 컬렉션 가치 | 반영 |
| 최근 가격변화 | 컬렉션 설명, 자랑, 링크 클릭 유도에 반영 |
| 카드 자랑/공유 | 반영 |
| Renaiss 링크 클릭 미션 | 반영 |
| 고가 카드 획득 알림 | 반영 |
| 전투력 | 직접 반영하지 않음 |
| IV/업적 성장 | 기존 게임 로직 유지 |

추천 성장 구조:

```text
전투력 = 카드 보유량 + IV + 업적/중복 성장 + 티어/희귀도
컬렉션 가치 = Renaiss 기준가 합산
시장 관심도 = 최근 가격변화 + Renaiss 링크 클릭률 + 인기 카드 여부
```

이렇게 나누면 게임 재미와 시장 데이터가 서로 망치지 않는다.

---

## 9. 개발 우선순위

### P0: 해커톤 데모 필수

1. 카드팩 결과 대표 카드 1장에 Renaiss 상단바 렌더링
2. Renaiss 가격/링크 필드 DB 추가
3. 레퍼럴 URL 빌더 추가
4. Telegram inline button으로 `Renaiss에서 가격 보기` 제공
5. 링크 클릭 로그 저장
6. 가격 조회 실패 fallback 처리
7. 최근 가격변화 표시. 기본은 7일 변화율, 데이터가 없으면 `-`

### P1: 실제 유입 강화

1. 카드팩 결과 상위 3장까지 Renaiss 상단바
2. 내 컬렉션 가치 합산
3. 오늘의 최고가 pull 랭킹
4. 최근 상승 카드 pull 랭킹
5. Renaiss 링크 클릭 미션
6. 관리자 매칭 화면

### P2: 운영 최적화

1. 가격 캐시 갱신 스케줄러
2. 카드별 클릭률 리포트
3. 고가 카드 알림
4. Renaiss 가격 변동 알림
5. 24시간/7일/30일 가격변화 추세 저장
6. 추천 카드팩/이벤트 자동 생성

---

## 10. 성공 지표

| 지표 | 목표 |
|---|---:|
| 카드팩 개봉 결과 응답 | 10초 이내 |
| 대표 카드 Renaiss 가격 매칭률 | 60% 이상 |
| Renaiss 링크 클릭률 | 20% 이상 |
| 가격변화 표시 가능 카드 비율 | 50% 이상 |
| 팩 개봉 후 컬렉션 페이지 이동률 | 30% 이상 |
| 가격 조회 실패로 인한 렌더링 실패 | 0건 |

---

## 11. 최종 제품 문장

```text
텔레그램에서 TCG 카드팩을 열고,
희귀 카드가 나오면 Renaiss 기준가와 레퍼럴 링크로 실제 시장 가치를 확인하는 게임.
P0는 포켓몬 TCG로 시작하고, Renaiss API가 지원하는 원피스/기타 카드로 확장한다.
```

해커톤 제출용 영어 문장:

```text
DropRoom turns a Telegram TCG gacha game into a Renaiss-powered collector funnel: users open packs in chat, see Renaiss FMV overlays on their best pulls, and click referral-linked card pages to explore real collectible market value. It starts with Pokémon cards and can expand to One Piece and other Renaiss-supported card categories.
```
