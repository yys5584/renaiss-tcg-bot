# Renaiss Bot Runbook

## 로컬 스모크 테스트

```powershell
$env:RENAISS_SKIP_DB="1"
python -m compileall renaiss_bot
python -c "import asyncio; from renaiss_bot.services.pack import open_pack; from renaiss_bot.renderers.overlay import render_overlay_card; r=asyncio.run(open_pack(None, count=30)); b=render_overlay_card(r.best_card, r.best_price); print(r.pack_count, len(r.cards), r.best_card.card_name, r.best_price.status, len(b))"
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
python -c "import asyncio; from renaiss_bot.services.models import CardIdentity; from renaiss_bot.services.pricing import fetch_price; p=asyncio.run(fetch_price(CardIdentity(category='pokemon_tcg', card_name='Charizard ex', set_code='SV4a', collector_number='349/190', grade='PSA 10'))); print(p.status, p.source, p.fmv_usd, p.change_7d_pct)"
```

## 배포 전 체크

- `RENAISS_BOT_TOKEN`이 TGPOKE 본봇 토큰과 다른지 확인
- Discord를 켤 때 `RENAISS_DISCORD_TOKEN`이 별도 Discord bot token인지 확인
- `RENAISS_DISCORD_GUILD_ID`를 넣으면 해당 서버에 slash command가 빠르게 sync됨
- `RENAISS_REFERRAL_CODE`가 실제 referral 코드인지 확인
- `RENAISS_API_BASE_URL`, `RENAISS_API_SEARCH_PATH`, `RENAISS_API_KEY`가 공식 Renaiss 값인지 확인
- 운영 DB를 쓸 때 `DATABASE_URL`을 넣고 시작 로그에 `Renaiss DB tables ready.`가 찍히는지 확인
- `/open 30`이 10초 안쪽으로 응답하는지 확인
- `/open one_piece_tcg 10`이 One Piece 카드풀로 응답하는지 확인
- `/mycards`가 고유 카드, 총 보유량, 등급 분포, 상위 카드, 최근 획득을 보여주는지 확인

## 카탈로그 Import

```powershell
python -m renaiss_bot.tools.import_catalog_json .\onepiece_cards.json --category one_piece_tcg
```

입력 JSON은 배열이어야 합니다. `name`, `id`, `set_code`, `collector_number`, `grade`, `image_url`, `fmv_usd` 같은 필드를 인식합니다.

## Discord 실행

```powershell
python -m pip install -r renaiss_bot\requirements-discord.txt
python -m renaiss_bot.adapters.discord.main
```

Windows 수동 실행:

```powershell
renaiss_bot\start_renaiss_discord.bat
```

## 확장 순서

1. Telegram P0: Pokemon TCG 카드팩, FMV 상단바, referral 버튼
2. 운영 DB `cards` 테이블 기반 카드풀 확인
3. Renaiss API exact/candidate/search_only/missing 상태 연결
4. One Piece TCG 카테고리 활성화
5. Discord adapter 추가
