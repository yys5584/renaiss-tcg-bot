"""Renaiss 플렉스 카드 스토리/캡션 단위 테스트 (DB 불필요)."""

from __future__ import annotations

from renaiss_bot.services.flex import build_flex_caption, grade_rarity_phrase


def test_grade_rarity_phrase_rare_grades():
    # 럭키슬롯 확률이 낮은 등급은 '1-in-N' 문구가 붙는다
    assert "1-in-" in (grade_rarity_phrase("MUR") or "")
    assert "1-in-" in (grade_rarity_phrase("UR") or "")
    assert "1-in-" in (grade_rarity_phrase("SAR") or "")


def test_grade_rarity_phrase_common_grades_none():
    # 너무 흔하거나 확률표에 없는 등급은 스토리 생략
    assert grade_rarity_phrase("R") is None      # 0.929 → 너무 흔함
    assert grade_rarity_phrase("C") is None       # 확률표에 없음
    assert grade_rarity_phrase("") is None


def test_grade_rarity_phrase_case_insensitive():
    assert grade_rarity_phrase("mur") == grade_rarity_phrase("MUR")


def test_build_flex_caption_includes_core_fields():
    caption = build_flex_caption(
        display_name="Moonyu",
        card_name="Charizard ex",
        grade="SAR",
        market_usd=430.0,
        portfolio_usd=1240.0,
        change_7d_pct=3.2,
    )
    assert "Moonyu" in caption
    assert "Charizard ex" in caption
    assert "SAR" in caption
    assert "$430" in caption
    assert "$1,240" in caption
    assert "3.2%" in caption
    assert "Props" in caption


def test_build_flex_caption_hides_zero_values():
    caption = build_flex_caption(
        display_name="Rook",
        card_name="Snorlax",
        grade="R",
        market_usd=0,
        portfolio_usd=0,
    )
    assert "Rook" in caption
    assert "Snorlax" in caption
    # 시세 0 이면 Market value 줄이 없어야 한다
    assert "Market value" not in caption
    assert "collection" not in caption


def test_build_flex_caption_escapes_html():
    caption = build_flex_caption(
        display_name="<b>hax</b>",
        card_name="A & B <script>",
        grade="R",
        market_usd=10,
        portfolio_usd=10,
    )
    assert "<b>hax</b>" not in caption
    assert "&lt;b&gt;hax&lt;/b&gt;" in caption
    assert "&amp;" in caption
