# Renaiss 봇 "TCG 가이드" 기획 — 2026-07-07

> **역사적 참고 · 2026-07-11 확정:** 이 문서의 RP 구매, 프리미엄팩 보상,
> 드랍 `d/f`, 포트폴리오 수익률 경쟁은 현재 제품 규칙이 아니다. 기본 파일럿은
> `c → blind guess → reveal`과 별도 Daily Pick이며 레거시 팩 경제는 꺼져 있다.
> 최신 기준은 `GAME_PHILOSOPHY.md`와
> `RENAISS_COLLECTOR_MARKET_CHALLENGE_PLAN_2026-07-10.html`이다.

> 사장님 컨펌 방향: **금액(시세)을 전면에 + TCG 가이드 정체성**.
> 확정 3기능: ① 포트폴리오 수익률 ② 그레이딩 프리미엄 ③ 데일리 가격 퀴즈(메인 그룹).
> 이 문서는 기획 1장. 구현은 사장님 컨펌 후 시작.

---

## 컨셉 한 줄

**"공짜 카드 게임으로 놀다 보면, 실제 TCG 시세를 알게 되는 봇."**
게임(드랍/카드팩)은 이미 있음 → 여기에 실시세(Renaiss Index API) 레이어를 얹어서 가이드로 완성.

---

## 지금 있는 것 (재사용 자산)

| 자산 | 위치 | 상태 |
|---|---|---|
| 블라인드 스폰/카드팩 (`c` 흐름) | `handlers/spawn.py`, `handlers/cardpack.py` | 동작 |
| `/price` 시세 조회 (FMV/등급/7일 변동) | `handlers/price.py`, `services/pricing.py` | 동작 |
| 시세 스냅샷 저장 | `services/pricing.py::_store_snapshot` | 있음 (수익률 계산의 기반) |
| 슬랩 라벨 카드 이미지 (PSA/BGS/CGC 색상) | `renderers/overlay.py` | 동작 |
| 포트폴리오 점수 (총 시세 USD + 다양성 + 업적) | `services/portfolio.py` | 동작 — 변동률만 없음 |
| 레퍼럴 버튼 | `services/referral.py` → `renaiss.xyz/ref/moonyu` | 동작 |

---

## ① 포트폴리오 수익률 (코인 지갑 느낌)

**뭐가 바뀌나**: 포트폴리오 화면에 "얼마"만 있던 걸 "얼마나 올랐나"로.

- 표시: `My Collection: $1,240 (7d +$38, +3.2%)` + 최고 효자 카드 1장 (`Charizard +12%`)
- 주간 리포트: 주 1회 포트폴리오 요약 카드 이미지 (총액/변동/효자카드) → 자랑용
- 필요한 것:
  - 유저 보유 카드 시세 **일일 스냅샷 잡** (기존 `_store_snapshot` 확장, 하루 1회)
  - 스냅샷 비교로 7일 변동 계산
- 가격 없는 카드(`unpriced`)는 변동 계산에서 제외하고 개수만 표기 (이미 구분 필드 있음)

## ② 그레이딩 프리미엄 가이드

**뭐가 바뀌나**: `/price` 와 팩 결과에 RAW vs 그레이드 가격 갭 한 줄 추가.

- 표시: `RAW $45 → PSA 10 $390 (8.7x grading premium)`
- 같은 카드의 그레이드별 검색 결과를 묶어서 비교 (Index API `gradeLabel`/`company` 필드 이미 매핑됨)
- 버튼: "See graded slabs on Renaiss" → 레퍼럴 링크
- 르네이스가 제일 좋아할 기능 — 그쪽 핵심 상품(그레이딩 슬랩)의 가치를 봇이 매일 설명해주는 셈

## ③ 데일리 가격 퀴즈 (메인 그룹)

**전제: 르네이스 봇 공식 메인 그룹 개설 필요 (아직 없음 — 사장님이 만들면 봇 초대 + `RENAISS_QUIZ_CHAT_ID` 설정)**

**확정 (사장님 2026-07-07):**
- **매일 21:00 KST** 출제 (초기엔 사장님이 직접 볼 수 있는 시간. 영어권 반응 보고 이동 가능)
- 4지선다 버튼, **120초** 마감 (`RENAISS_QUIZ_OPEN_SECONDS`)
- 보상: **정답자 전원 프리팩 1개 + RP 100** / **정답자 중 랜덤 1명 프리미엄팩** (잭팟)
  - 첫 정답자 우대 없음 — 속도 경쟁은 알림 스나이핑 고인물만 유리해서 랜덤으로
- **주간 순위** (월~일 KST 정답 수): 매일 정답 공개 메시지에 TOP 5 표시
- 정답 공개: 실시세 슬랩 라벨 카드 이미지 + 르네이스 레퍼럴 버튼
- 출제 규칙: 시세 있는 카드만 (최소 $5), Index 시세 실패 시 카탈로그 시세 폴백
- 문제 이미지는 시세 라벨 **없는** 원본 카드 (오버레이는 가격이 노출되므로 정답 공개 때만)

---

## 리스크 / 지켜야 할 선

1. **Index API는 베타** — 시세 누락/지연 가능. 모든 금액 표기에 "reference data" 유지 (기존 문서 방침 그대로).
2. **투자 권유 아님** — "시세를 알려주는 가이드"까지만. 수익 보장/추천 문구 금지.
3. **영어판 `c` 흐름 유지** — catch의 한 글자 진입점을 복잡하게 만들지 않음 (`AGENTS.md` 절대 규칙).
4. 퀴즈 알림은 하루 1회 고정 — 도배 금지.

---

## 구현 상태 (2026-07-07 완료)

| 기능 | 파일 | 상태 |
|---|---|---|
| ② 그레이딩 프리미엄 | `services/grading.py`, `services/client.py::fetch_grade_offers`, `handlers/price.py` | ✅ |
| ① 포트폴리오 수익률 | `database/queries.py` (스냅샷), `services/portfolio.py`, `handlers/cardpack.py`, `jobs.py` (23:55 KST 일괄) | ✅ |
| ③ 데일리 퀴즈 | `services/quiz.py`, `handlers/quiz.py`, `jobs.py` (21:00 KST), `database/schema.py` (테이블 3개) | ✅ 코드 완료, 그룹 개설 대기 |
| 재시작 복구 | `jobs.py::recover_open_quiz_rounds` — 정산 안 된 라운드 재스케줄 | ✅ |
| 단위 테스트 | `tests/test_renaiss_quiz.py` 19건 | ✅ 통과 |
| 블라인드 스폰 | `handlers/spawn.py` — `c` 추첨 + FMV 추측/공개 분포 | ✅ |
| Daily Market Pick 기반 | `services/market.py`, `database/market_queries.py`, `handlers/market.py` | ✅ 하루 한 장 잠금 / 매수·매도·수수료 제외 / T+24h·리그는 다음 단계 |

## 팩 경제 (2026-07-07 사장님 컨펌 — "운영 게임" 방향)

기존 `/open` 무제한 무료 → 게이트 도입. 희소성이 생겨야 보상/자랑/수집이 의미를 가진다.

| 항목 | 값 (초안 — 운영 보며 조절) |
|---|---|
| 일일 무료 프리팩 | 5팩/일 (KST 리셋, 명령 개봉 기준) |
| 추가 프리팩 | `/open rp` — 100 RP/팩 |
| 프리미엄팩 | `/open premium` — 500 RP/팩 |
| 드랍/퀴즈 보상 팩 | 게이트와 무관 (별도 지급) |
| RP 수입 | 드랍 참여(비우승) +50 / 퀴즈 정답 +100 |

- RP 순환: 드랍 10회 참여 or 퀴즈 5정답 = 프리미엄팩 1개.
- `/mycards` 에 RP 잔액 표시. `/pack` 가이드에 경제 안내.
- 다음 단계(미착수): 세트 컴플리트 목표 + 컴플 보상.

## 남은 것 (사장님)

- [ ] 메인 그룹 개설 → 봇 초대 → 그룹 chat id를 `RENAISS_QUIZ_CHAT_ID` 에 설정
- [ ] 르네이스 상금 제안 발송 여부 → `RENAISS_QUIZ_SPONSORSHIP_PROPOSAL_2026-07-07.md`
