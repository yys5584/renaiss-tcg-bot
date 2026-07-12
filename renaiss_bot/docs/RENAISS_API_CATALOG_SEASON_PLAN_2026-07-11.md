# Renaiss API 카탈로그 기반 시즌 운영안

> 기준 시각: 2026-07-11 KST  
> 제품 범위: 영어권 Telegram 공개방용 Renaiss Collector Bot  
> 금지 범위: 매수·매도·포지션·수수료·현금성 보상 없음

## 1. 확인된 API 콘텐츠 규모

공식 API OpenAPI에는 전체 카탈로그 목록이나 `total/page/offset` endpoint가 없다.
`/v1/search`는 최대 30개 결과만 반환하고 전체 수를 반환하지 않는다.

따라서 전체 주소화 카드 수는 공식 `https://index.renaissos.com/sitemap.xml`의
`/card/{game}/{set}/{card}` URL을 기준으로 집계한다.

| 구분 | 카드 페이지 | set slug |
|---|---:|---:|
| Pokémon | 11,559 | 822 |
| One Piece | 2,186 | 233 |
| 합계 | 13,745 | 1,055 |

위 수치는 2026-07-11 KST에 sitemap 캐시 우회 요청 8회를 보내 동일 SHA-256 응답에서
재확인한 고유 카드 **상세 페이지 URL 수**다. 같은 sitemap에는 Pokémon 세트 목록
787개와 One Piece 세트 목록 102개도 있어 `/card/`가 들어간 URL을 깊이 구분 없이 세면
14,634개가 된다. 즉 `14,634 = 카드 상세 13,745 + 세트 목록 889`이며 14,634장을
뜻하지 않는다. 또한 13,745도 언어·variation을 포함한 페이지 수라 감정 등급별 API
자산 총수나 고유 일러스트 수와 동일하다고 단정하지 않는다. 카탈로그 생성 시 sitemap
URL과 응답 해시·조회 시각·경로 깊이를 함께 기록한다.

Pokémon URL의 언어 표기 분포:

| 표기 | 카드 페이지 |
|---|---:|
| 무표기/기본 언어 | 7,006 |
| Japanese | 4,388 |
| Chinese | 144 |
| Korean | 12 |
| 기타 유럽 언어 | 9 |

이 수치는 카드 페이지 수다. 같은 카드의 언어·variation은 별도 페이지일 수 있으며,
감정등급별 가격 행 수나 현재 Daily Pick eligibility 수와 동일하지 않다.

### PSA 10 전체 감사

2026-07-11 KST에 sitemap의 카드 상세 페이지에서 1,055개 set slug를 추출하고,
`/v1/sets/{game}/{set}`을 전수 조회했다. 1,026개 endpoint가 200, 29개가 404였으며
429나 최종 요청 실패는 없었다. endpoint alias 중복은 `href`로 제거했다.

| 항목 | Pokémon | One Piece | 합계 |
|---|---:|---:|---:|
| 고유 API 시장 자산 | 33,506 | 4,855 | 38,361 |
| PSA 10 자산 | 30,895 | 4,674 | 35,569 |
| 양수 가격이 있는 PSA 10 | 7,324 | 1,335 | 8,659 |
| PSA 10 양수가격 비율 | 23.71% | 28.56% | 24.34% |

따라서 sitemap의 13,745개는 API의 PSA 10 자산 수가 아니다. sitemap은 카드 상세
페이지의 공개 인덱스이고, set API는 grading company·grade가 붙은 시장 자산을 더 많이
반환한다. PSA 10 기준 결과는 다음과 같다.

- 35,569개 모두 `imageUrl`이 있었고 URL query를 제거한 이미지 path도 35,569개로
  서로 달랐다. 단, 실제 이미지 바이트를 전부 내려받아 perceptual hash를 비교한 것은
  아니므로 시각적으로 완전히 다른 그림이라고 단정하지 않는다.
- 양수 가격은 8,659개만 있었다. 나머지 26,910개는 가격이 없거나 0이어서 가격 기반
  시즌 후보로 바로 사용할 수 없다.
- 양수 가격 8,659개에는 6,708개의 서로 다른 가격값이 있었다. 같은 가격을 가진 자산이
  1,951개 더 있으므로 가격 숫자도 전부 고유하지 않다.
- `game·setCode·cardNumber·name·variation·language` 기준 canonical identity는
  34,885개였다. PSA 10 href 35,569개 중 684개는 이 필드 조합이 겹치므로 hash·추가
  variation 근거를 확인한 뒤 중복 여부를 결정해야 한다.

첫 시즌 1,000장 모집단은 13,745 sitemap 페이지 전체가 아니라 **양수 가격 PSA 10
8,659개**에서 시작한다. 그다음 canonical 중복, 가격 freshness·confidence·source,
90일 거래량, 이미지 실재 여부를 통과시켜 최종 1,000장을 만든다.

별도로 `/v1/indices/pokemon`은 가장 많이 거래된 Pokémon 50개를 constituent로 제공한다.
이 50개는 시즌 검증 풀의 좋은 seed지만 자동으로 Daily Pick 가능하다는 뜻은 아니다.

## 2. 비싼 카드 우선 Top 1000

sitemap 카드 수가 큰 20개 Pokémon 세트를 set listing API로 조회했다. 3,697개의
고유 PSA 10 asset 중 양수 가격이 있는 카드는 1,165개였으며, 가격 상위 1,000개를
가격 기준 Season Master Pool 초안으로 만들었다. 전체 감사에서 양수가격 PSA 10이
8,659개로 확인됐으므로 이 초안은 최종 풀이 아니며 거래량 혼합 선정을 다시 수행한다.

| 순위 컷 | API 가격 |
|---|---:|
| 1위 | $85,400.00 |
| 28위 | $6,512.32 |
| 100위 | $999.00 |
| 250위 | $249.30 |
| 500위 | $93.14 |
| 750위 | $50.89 |
| 1,000위 | $32.60 |

Top 1000의 가격 분포:

| 가격대 | 카드 수 |
|---|---:|
| $1,000+ | 99 |
| $500~999 | 66 |
| $300~499 | 58 |
| $100~299 | 258 |
| $50~99 | 277 |
| $32.60~49 | 242 |
| 합계 | 1,000 |

이 1,000장은 시즌 수집 후보 풀이다. set listing 가격이 있다는 이유만으로 가격 추측이나
Daily Pick에 자동 사용하지 않는다. 경쟁용 가격은 detail API 검증을 별도로 통과해야 한다.

### 가격순 1,000장의 한계와 거래량 보정

set listing에는 거래 건수가 없으며, 카드별
`/v1/cards/{game}/{set}/{card}/trades?window=90&scope=grade&limit=1`의 `total`을
조회해야 실제 90일 거래량을 알 수 있다.

실측 예시:

| 카드 | API 가격 | 최근 90일 거래 |
|---|---:|---:|
| Skyridge Charizard 146 PSA 10 | $85,400.00 | 19건 |
| Shiny Star V Toxel PSA 10 | $32.60 | 0건 |

따라서 가격순 manifest는 후보 원장으로만 사용하고 최종 Master Pool은 다음의 배타적
쿼터를 순서대로 채운다.

| 쿼터 | 카드 수 | 기준 |
|---|---:|---|
| High Value | 250 | 가격 상위, 0달러 제외 |
| High Volume | 300 | 최근 90일 거래량 상위, High Value와 중복 제외 |
| Cheap Active | 250 | $5~50이면서 최근 90일 거래 1건 이상 |
| Diversity | 200 | 캐릭터·세트 편중을 줄이는 나머지 카드 |
| 합계 | 1,000 | 한 카드가 두 쿼터를 중복 점유하지 않음 |

편중 제한:

- 같은 card name은 Master Pool 최대 12장
- 같은 set은 Master Pool 최대 80장
- 동일 identity는 1장
- 0달러·href 누락·PSA 10이 아닌 자산은 제외

API 부하를 막기 위해 1,000장의 trade endpoint를 매일 전수 조회하지 않는다. 최초
manifest 생성 시 한 번 감사하고, 운영 중에는 전체 풀을 7일 배치로 나눠 하루 약
143장씩 비동기 갱신한다.

### Verified Grail seed 28

Pokémon Index Top 50을 현재 API 가격 `$100` 이상으로 자르면 35장이 남는다.
35장의 detail API를 다시 조회한 결과 28장이 exact·median·confidence·source·freshness
조건을 모두 통과했다.

| 가격대 | Top 50 내 카드 | 최종 Verified |
|---|---:|---:|
| $1,000+ | 4 | 3 |
| $500~999 | 7 | 5 |
| $300~499 | 8 | 7 |
| $100~299 | 16 | 13 |
| 합계 | 35 | 28 |

탈락한 7장도 exact median과 prime confidence는 있지만 source count가 1이라
시즌 가격 판정에는 사용하지 않는다. source가 2개 이상으로 회복되면 다음 시즌
후보로 재검증한다.

### 검증 seed 목록

| 가격대 | 카드 |
|---|---|
| $1,000+ | Pikachu with Grey Felt Hat SVP 085 · Pikachu Ex MC 764 · Mega Charizard X Ex M2 110 |
| $500~999 | Mew Ex SV4A 347 · Charizard-Holo S8A-P 001 · Umbreon Ex SV8A 217 · Mega Dragonite ex M2A 250 · Pikachu Ex M2A 234 |
| $300~499 | Detective Pikachu SV-P 098 · M Rayquaza Ex S8A-P 024 · Mega Greninja ex M4 114 · Pikachu S-P 208 · Mega Dragonite Ex M2A 246 · Magikarp SV1A 080 · Tohoku's Pikachu SV-P 260 |
| $100~299 | Rocket's Mewtwo Ex M2A 237 · Pikachu CLL 008 · Sylveon Ex SV8A 212 · Charizard Vstar S12A 212 · Meowth ex M3 114 · Pikachu S10A 073 · Mew S12A 183 · Mega Charizard X ex M2A 223 · Espeon Ex SV8A 211 · Pikachu SV2A 173 · Leafeon Ex SV8A 200 · Pikachu SV-P 001 · Pikachu SV-P 242 |

Watch-only 7장:

- Pikachu S-P 227
- Birthday Pikachu-Holo S8A-P 007
- Mega Gengar Ex M2A 240
- Umbreon-Gold Star S8A-P 012
- Fukuoka's Pikachu SV-P 289
- Mega Charizard X EX MEP 023
- Oricorio ex MEP 024

## 3. Season 0 — Top 1000

### Season 1 실측 노출량

`일 6스폰`은 초기 파일럿의 무인 안전 캡이었을 뿐, 시즌 공급량의 근거가 아니다.
TGPoke 운영 DB의 2026년 3월 KST 로그를 다시 집계한 결과 Season 1은 방 활동량과
고속 스폰 세션에 따라 노출량이 크게 달라지는 구조였다.

| Season 1 실측 항목 | 결과 |
|---|---:|
| 전체 스폰 | 289,180회 |
| 스폰 발생 방 | 234개 |
| 전체 일평균 | 9,639회 |
| 활성 방-일 중앙값 | 23회 |
| 활성 방-일 p90 | 245회 |
| 최다 방 스폰 | 73,367회 / 활성 29일 |
| 최다 방 일평균·중앙값 | 2,530회 · 2,598회 |
| 최다 방 스폰 간격 중앙값 | 약 30초 |
| 2위 방 스폰 | 46,706회 / 활성 29일 |

따라서 Renaiss도 모든 방에 동일한 작은 일일 수량을 배급하는 모델로 잡지 않는다.
공식 고활성 방은 Season 1과 비슷한 연속 노출을 목표로 하고, 일반 방은 실제 활동량에
따라 노출된다. `일 6회`와 `시즌 최대 540회` 가정은 폐기한다.

### 수정된 공개방 공급 모델

| 항목 | 기준 |
|---|---:|
| Master Pool | 거래가치·거래량·저가 활성도를 섞은 1,000장 이상 |
| 공식 고활성 방 목표 | 30초급 연속 라운드, 운영 상태에 따라 감속 |
| 일반 방 목표 | 활동 기반, 고정 일일 할당 없음 |
| 최다 방 실측 환산 | 약 2,530스폰/일 |
| 1,000장 1회전 | 약 9.5시간 |
| 30일 공식방 노출 환산 | 약 75,900회, 카드당 평균 약 75.9회 |
| 현재 Verified seed | 28장 |
| Daily Pick 후보 | 매일 검증 카드 3장 |

1,000장은 장기간 조금씩 공개할 덱이 아니라 고빈도 공식방에서 반복 회전할 최소 풀이다.
주간 Active Deck 120장만 스폰에 쓰면 최다 방 기준 카드당 하루 약 21회 반복되어
피로도가 너무 높으므로, Collection 스폰은 Master Pool 전체를 셔플백으로 사용한다.
한 백을 다 쓰기 전에는 같은 canonical identity를 다시 뽑지 않고, 백을 새로 만들 때
High Value 250, High Volume 300, Cheap Active 250, Diversity 200 비율을 유지한다.

Daily Pick은 Collection 스폰 빈도와 분리한다. 하루에 검증 카드 3장을 별도 후보로
고정하며, Collection 스폰에서 같은 카드가 여러 번 등장해도 Daily Pick 선택 횟수나
점수가 늘어나지 않는다. 검증 카드가 3장 확보되지 않은 날은 Daily Pick을 열지 않는다.

### 고빈도 운영을 위한 데이터 경로

스폰마다 Renaiss detail/trades API를 동기 호출하면 Season 1 수준의 노출에서 API 한도와
지연이 핵심 루프를 막는다. 시즌 manifest의 canonical identity, 마지막 검증 가격,
confidence, source count, freshness, 90일 거래량을 DB에 저장하고 스폰 경로는 DB만 읽는다.

- 가격·거래량 갱신은 별도 비동기 작업이 API rate limit 안에서 수행한다.
- freshness가 만료된 카드는 Collection에는 남겨도 가격 추측과 Daily Pick에서는 즉시 제외한다.
- 카드 이미지는 Telegram `file_id` 캐시를 우선 사용하고, 캐시 미스일 때만 원본 이미지를 가져온다.
- API 429·timeout 동안에도 캐시된 Collection 스폰은 계속되며 검증 가격 기능만 축소된다.
- 한 인스턴스만 갱신 lease를 잡고, 스폰 프로세스는 외부 API 완료를 기다리지 않는다.

구현 cadence는 03:00·15:00 KST 하루 2회다. 각 실행은 활성 시즌 카드 최대 1,000장의
exact detail 근거를 갱신해 `renaiss_catalog_cards`에 저장한다. 공개 스폰은 Partner API를
직접 호출하지 않고 이 테이블만 읽는다. 갱신 실패 시 마지막 성공값을 보존하되 48시간이
지나면 가격 추측·Daily Pick에서 자동 제외한다. 이미지 URL도 같은 행에 저장하고 실제
Telegram 전송은 `file_id` 캐시를 우선해 동일 이미지를 반복 다운로드하지 않는다.

### 중복 규칙

- 일반 Collection 카드: 같은 1,000장 셔플백 안에서는 최대 1회 공개한다.
- 백을 모두 소비한 뒤에는 새로 섞어 재등장할 수 있다.
- Daily Pick의 Verified 카드: 후보 재사용 최소 7일 간격, 30일당 최대 3회
- Watch-only 카드는 수집에는 사용할 수 있지만 Daily Pick에서는 제외한다.
- 같은 이름이라도 세트·번호·언어·variation이 다르면 다른 카드로 본다.
- 신규 유저의 당첨 확률은 과거 수집량과 무관하게 유지한다.

### 주차 구성

1. **Month 1 — Discovery**: Top 1000의 캐릭터·세트 폭을 보여준다.
2. **Month 2 — Set Sense**: 같은 캐릭터의 다른 세트와 가격 차이를 학습한다.
3. **Month 3 — Conviction**: 검증 카드 재등장과 시즌 안목 결과를 공개한다.

## 4. API 우선순위

1. 스폰 후보의 canonical identity를 만든다.
2. Renaiss card detail API exact 조회를 먼저 실행한다.
3. API exact가 있으면 FMV와 근거 표시는 무조건 API 값을 사용한다.
4. eligibility 미달이면 `watch only`로 표시하고 경쟁에서 제외한다.
5. API exact가 없을 때만 로컬 카탈로그 가격을 수집용 폴백으로 사용한다.
6. candidate 검색 결과를 exact나 Daily Pick 가격으로 승격하지 않는다.

## 5. 출시 전 데이터 작업

API에 전체 eligibility count endpoint가 없으므로 season manifest를 사전에 만든다.

1. 20개 대형 세트의 양수 가격 PSA 10 카드를 가격순 정렬한다.
2. 상위 1,000장을 collection manifest에 넣는다.
3. 상위 가격대부터 detail API를 순차 검증한다.
4. 최소 90장이 eligibility를 통과해야 90일 Daily Pick 시즌을 연다.
5. 현재 확인된 28장은 Verified seed로 사용한다.
5. 시즌 중 freshness가 만료되면 자동으로 Insight Pool에서 제외한다.
6. 매일 최소 3개가 남지 않으면 Daily Pick을 닫고 운영자에게 알린다.

Renaiss 측에 요청할 우선 endpoint:

- 전체 카드/세트 수
- game·language·grade별 count
- pagination 가능한 catalog listing
- exact/median/confidence/source/freshness 필터
- `updated_since` 증분 동기화

## 6. 성공 지표

- 스폰당 고유 `c` 참여자
- 참가자 중 가격 추측 전환율
- 하루 3개 Verified 후보 확보율
- Daily Pick 선택률과 T+24h 결과 열람률
- 신규 유저 D1/D7 재참여
- 그룹 내 명령 메시지 비중과 자동 삭제 성공률
- API exact match율, eligibility 통과율, 429·timeout 비율
- Master Pool의 90일 거래량 분포와 0거래 카드 비율

## 7. 반박과 결론

11,559개 전체는 너무 넓고 Grail 28만으로는 수집 발견성이 너무 작다. 반면 Top 1000은
현재 API 가격 하한이 $32.60이라 저가·가격 누락 자산을 제외하면서도 충분한 다양성을
제공한다.

따라서 첫 시즌은 **가격·거래량·저가 활성 카드가 섞인 1,000장 이상 Master Pool +
Season 1형 고빈도 셔플백 노출 + 비동기 가격·거래량 갱신**으로 운영한다. 시즌 기간은
노출량과 리텐션 실측 뒤 확정하며, `일 6회`를 근거로 90일을 고정하지 않는다.

### 심각 이슈 감사

- **P0 — 스폰 hot path**: 현재 구현은 exact API 설정 시 스폰 직전에 카드 detail API를
  조회한다. 30초 프로필을 켜기 전에 DB 캐시만 읽도록 전환하고 API 갱신 worker를
  분리해야 한다.
- **P0 — 셔플백 원자성**: 현재 랜덤 밴드 추첨은 1,000장 1회전 내 중복 방지를 보장하지
  않는다. 다중 인스턴스에서도 한 카드가 중복 소비되지 않는 DB cursor/lease가 필요하다.
- **P0 — 실제 manifest**: 생성된 가격순 1,000장 초안은 전부 Collection 후보이며
  거래량 감사와 운영 DB import가 끝나지 않았다. 이 상태로 고빈도 시즌을 열 수 없다.
- **P1 — Verified 수량**: 현재 28장으로는 3후보/일·30일·카드당 최대 3회 규칙의
  90개 슬롯을 채우지 못한다. 산술 최소 30장이고 freshness 탈락 여유를 포함해 45장
  이상을 확보해야 한다.
- **P1 — 그룹 피로도**: Season 1 최다 방의 30초 노출은 실측 근거가 있지만 Renaiss는
  가격 버튼과 공개 정보가 더 많다. 30초/45초 코호트의 참여율, mute·이탈, 명령 삭제율을
  비교하고 자동 감속 기준을 둔다.
- **P2 — 방별 적응형 cadence**: 공식방 고정 30초 이후 일반방 확장 시에는 최근 참여자와
  포획률에 따라 30초·1분·5분·휴면 단계를 오가는 방식이 고정 캡보다 낫다.

현재 판단은 **고빈도 프로필 배포 불가**다. 런타임이 30초·2,880회 설정을 수용하도록
상한과 포획 창은 수정했지만, 위 세 P0가 해소될 때까지 실제 운영 설정은 파일럿 안전
프로필에 둔다.
단, 현재 Verified seed는 28장뿐이므로 detail 검증 풀을 90장까지 확대하기 전에는 전체
Daily Pick 시즌을 시작하지 않는다.
