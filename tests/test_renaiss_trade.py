"""트레이딩 Lv2 — 매도가 계산·키보드 단위 테스트 (DB 불필요)."""

from __future__ import annotations

from renaiss_bot.database.queries import SELL_RATE
from renaiss_bot.handlers.trade import _sell_keyboard


def test_sell_rate_is_discount():
    # NPC 매입율은 시세보다 낮아야 (마진 + 인플레 방지)
    assert 0 < SELL_RATE < 1


def test_sell_keyboard_shows_discounted_proceeds():
    cards = [
        {"local_card_id": "a", "card_name": "Charizard ex", "market_price_usd": 400.0},
        {"local_card_id": "b", "card_name": "Pikachu", "market_price_usd": 100.0},
    ]
    kb = _sell_keyboard(cards)
    rows = kb.inline_keyboard
    assert len(rows) == 2
    # 400 * 0.6 = 240
    assert "240" in rows[0][0].text
    assert rows[0][0].callback_data == "renaiss:sell:a"
    # 100 * 0.6 = 60
    assert "60" in rows[1][0].text


def test_sell_keyboard_callback_prefix():
    cards = [{"local_card_id": "xyz", "card_name": "Mew", "market_price_usd": 50.0}]
    kb = _sell_keyboard(cards)
    assert kb.inline_keyboard[0][0].callback_data == "renaiss:sell:xyz"
