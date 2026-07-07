# Renaiss Bot

TGPOKE와 분리한 Renaiss Edition 봇입니다. P0는 Telegram에서 Pokemon TCG 카드팩을 열고, 대표 상위 카드에 Renaiss FMV 상단바와 referral 버튼을 붙이는 흐름입니다.

## 설치

```powershell
cd C:\Users\Administrator\Desktop\pokemon-bot
python -m pip install -r renaiss_bot\requirements.txt
```

## 환경변수

`renaiss_bot\.env.example`를 기준으로 루트 `.env` 또는 실행 환경에 값을 넣습니다.

필수:

```text
RENAISS_BOT_TOKEN=...
```

Discord를 같이 실행할 때:

```text
RENAISS_DISCORD_TOKEN=...
RENAISS_DISCORD_GUILD_ID=...
```

선택:

```text
DATABASE_URL=postgresql://user:pw@host:5432/db
RENAISS_SKIP_DB=1
RENAISS_BASE_URL=https://www.renaiss.xyz
RENAISS_REFERRAL_CODE=...
RENAISS_REFERRAL_PARAM=ref
RENAISS_API_BASE_URL=...
RENAISS_API_SEARCH_PATH=/api/search
RENAISS_API_KEY=...
RENAISS_PRICE_CACHE_TTL_MINUTES=10
```

`RENAISS_SKIP_DB=1`이면 DB 없이 샘플 카드풀과 렌더링만 실행합니다.

## 실행

```powershell
python -m renaiss_bot.main
```

Windows 수동 실행용:

```powershell
renaiss_bot\start_renaiss_bot.bat
```

Discord adapter 실행:

```powershell
python -m pip install -r renaiss_bot\requirements-discord.txt
python -m renaiss_bot.adapters.discord.main
```

## 명령

- `/start` : 홈 안내
- `/open` : Pokemon TCG 일반팩 1개 열기
- `/open premium` : Pokemon TCG 프리미엄팩 1개 열기
- `/open 30` : 일반팩 30개까지 batch 개봉
- `/open one_piece_tcg` 또는 `/open 원피스 10` : One Piece 카드팩 열기
- `/pack` : 카드팩 안내
- `/mycards` : 컬렉션 요약, 등급 분포, 상위 카드, 최근 획득
- `/price 리자몽` 또는 `가격 리자몽` : Pokemon 가격 확인
- `/price one_piece_tcg luffy` : One Piece 가격 확인
- `/sets` : 지원 카테고리

## Discord 명령

- `/open category:pokemon_tcg pack_type:free count:1`
- `/open category:one_piece_tcg count:10`
- `/price query:one_piece_tcg luffy`
- `/mycards`
- `/sets`

## 구조

- `services/card_pool.py` : 운영 DB의 `cards` 테이블 우선 사용, 실패 시 샘플 카드풀 fallback
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
python -m renaiss_bot.tools.import_catalog_json .\onepiece_cards.json --category one_piece_tcg
```

## 포함 문서

- `docs/RENAISS_TCG_PRICE_OVERLAY_PLAN_2026-06-28.md`
- `docs/RENAISS_BOT_SEPARATION_PLAN_2026-06-28.md`
- `docs/renaiss_overlay_render_examples.html`
- `docs/droproom_renaiss_design.html`
