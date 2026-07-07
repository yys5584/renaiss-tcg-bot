# Adapters

공유 로직은 `services/`, `database/`, `renderers/`에 둡니다.

플랫폼별 adapter는 입출력만 담당합니다.

- Telegram: `renaiss_bot.main` + `handlers/`
- Discord: `renaiss_bot.adapters.discord.main`

Discord는 Telegram P0와 같은 카드팩/가격/컬렉션 흐름을 slash command로 제공합니다.
