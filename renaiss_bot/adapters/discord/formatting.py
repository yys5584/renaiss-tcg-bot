"""Discord text formatting helpers."""

from __future__ import annotations

from collections import Counter

from renaiss_bot.services.models import PackOpenResult, RenaissPrice

_GRADE_ORDER = ("MUR", "UR", "SAR", "SR", "AR", "RR", "R", "U", "C")
_STATUS_LABELS = {
    "exact": "정확 매칭",
    "candidate": "후보 매칭",
    "search_only": "검색 링크",
    "missing": "가격 없음",
    "api_error": "API 오류",
}


def _price_line(price: RenaissPrice) -> str:
    if price.fmv_usd is not None:
        line = f"Renaiss 기준가: **${price.fmv_usd:,.0f}**"
        if price.change_7d_pct is not None:
            line += f" / 7일 {price.change_7d_pct:+.1f}%"
        return line
    if price.status == "candidate":
        return "Renaiss 후보 가격 확인 필요"
    if price.status == "search_only":
        return "Renaiss 검색 링크 제공"
    if price.status == "api_error":
        return "Renaiss API 응답 지연"
    return "Renaiss 가격 등록 대기"


def _grade_summary(result: PackOpenResult) -> str:
    counts = Counter(card.grade for card in result.cards)
    parts = [f"{grade} {counts[grade]}" for grade in _GRADE_ORDER if counts.get(grade)]
    return ", ".join(parts) if parts else "-"


def format_pack_message(result: PackOpenResult) -> str:
    best = result.best_card
    pack_label = "프리미엄팩" if result.pack_type == "premium" else "일반팩"
    return "\n".join(
        [
            "**Renaiss Edition 카드팩 개봉 완료**",
            "────────────",
            f"팩: **{pack_label}** x{result.pack_count} / 카드 {len(result.cards)}장",
            f"최고 카드: **{best.card_name}** {best.grade}",
            f"세트: {best.set_code or '-'} {best.collector_number or ''}".rstrip(),
            _price_line(result.best_price),
            f"등급 요약: {_grade_summary(result)}",
            f"카드풀: `{result.pool_source}`",
        ]
    )


def format_price_message(card_name: str, category: str, price: RenaissPrice) -> str:
    lines = [
        f"**{card_name}**",
        "────────────",
        f"카테고리: `{category}`",
        f"매칭 상태: `{_STATUS_LABELS.get(price.status, price.status)}`",
    ]
    if price.fmv_usd is not None:
        lines.append(f"Renaiss 기준가: **${price.fmv_usd:,.0f}**")
    if price.change_7d_pct is not None:
        lines.append(f"7일 가격 변화: **{price.change_7d_pct:+.1f}%**")
    if price.price_range_min_usd is not None and price.price_range_max_usd is not None:
        lines.append(f"후보 범위: ${price.price_range_min_usd:,.0f} - ${price.price_range_max_usd:,.0f}")
    lines.append(f"출처: `{price.source}`")
    return "\n".join(lines)


def format_collection_message(detail: dict | None) -> str:
    if not detail:
        return "아직 저장된 컬렉션이 없습니다. `/open`으로 카드팩을 열어주세요."

    lines = [
        "**내 Renaiss 컬렉션**",
        "────────────",
        f"고유 카드: **{detail['unique_cards']}**종",
        f"총 보유량: **{detail['total_quantity']}**장",
        f"카테고리: **{detail['categories']}**개",
        f"최고 기준가: **${float(detail.get('max_market_price') or 0):,.0f}**",
    ]

    grades = detail.get("grades") or []
    if grades:
        grade_text = ", ".join(f"{row.get('grade') or '-'} {int(row.get('count') or 0)}" for row in grades)
        lines.append(f"등급 분포: {grade_text}")

    top_cards = detail.get("top_cards") or []
    if top_cards:
        lines.extend(["", "**상위 보유 카드**"])
        for idx, row in enumerate(top_cards, 1):
            price = float(row.get("market_price_usd") or 0)
            price_text = f" / ${price:,.0f}" if price > 0 else ""
            quantity = int(row.get("quantity") or 0)
            qty_text = f" x{quantity}" if quantity > 1 else ""
            lines.append(f"{idx}. **{row.get('card_name') or '-'}** {row.get('grade') or '-'}{qty_text}{price_text}")

    recent_cards = detail.get("recent_cards") or []
    if recent_cards:
        lines.extend(["", "**최근 획득**"])
        for idx, row in enumerate(recent_cards, 1):
            quantity = int(row.get("quantity") or 0)
            qty_text = f" x{quantity}" if quantity > 1 else ""
            lines.append(f"{idx}. **{row.get('card_name') or '-'}** {row.get('grade') or '-'}{qty_text}")

    return "\n".join(lines)
