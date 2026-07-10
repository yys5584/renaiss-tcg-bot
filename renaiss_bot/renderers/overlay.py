"""Renaiss card image renderer."""

from __future__ import annotations

import re
from html import escape
from pathlib import Path
from typing import Any

from renaiss_bot.renderers.playwright_render import (
    render_html_to_png,
    resolve_image_to_data_uri,
)
from renaiss_bot.services.models import CardIdentity, RenaissPrice
from renaiss_bot.services.pack_rules import normalize_grade


_ROOT = Path(__file__).resolve().parents[1]
_LOGO_PATH = _ROOT / "assets" / "renaiss_logo.svg"
_LOGO_SVG_CACHE: str | None = None


def _card_payload(card: CardIdentity, price: RenaissPrice) -> dict[str, Any]:
    metadata = dict(card.metadata or {})
    metadata.pop("pokard_card_id", None)
    metadata.pop("card_id", None)
    image_url = price.image_url or card.image_url
    if image_url:
        metadata.setdefault("display_image_url", image_url)
        metadata.setdefault("image_url", image_url)

    return {
        "card_id": card.local_card_id,
        "species_id": card.species_id,
        "species_name": card.species_name or card.card_name,
        "name": card.card_name,
        "grade": card.grade or card.rarity or "RAW",
        "rarity": card.grade or card.rarity or "RAW",
        "original_rarity": card.rarity or card.grade or "RAW",
        "series_code": card.set_code,
        "series_name": card.set_name or card.set_code,
        "number": card.collector_number,
        "card_number": card.collector_number,
        "image_url": image_url,
        "display_image_url": image_url,
        "metadata": metadata,
        "is_new": not card.already_owned,
        "is_shiny": False,
    }


def _logo_svg() -> str:
    global _LOGO_SVG_CACHE
    if _LOGO_SVG_CACHE is not None:
        return _LOGO_SVG_CACHE
    try:
        svg = _LOGO_PATH.read_text(encoding="utf-8")
    except OSError:
        svg = '<span class="logo-fallback">renaiss</span>'
    svg = svg.replace('width="201" height="59"', 'width="100%" height="100%"')
    svg = svg.replace("currentColor", "#F2EAD2")
    _LOGO_SVG_CACHE = svg
    return svg


def _price_text(price: RenaissPrice) -> str:
    if price.fmv_usd is None:
        return "CHECK"
    if price.fmv_usd >= 1_000_000:
        return f"${price.fmv_usd / 1_000_000:.1f}M"
    if price.fmv_usd >= 1000:
        return f"${price.fmv_usd:,.0f}"
    return f"${price.fmv_usd:,.2f}"


def _grade_text(card: CardIdentity, price: RenaissPrice) -> str:
    grade = (price.grade_label or card.grade or card.rarity or "RAW").strip()
    if not grade:
        return "RAW"
    upper = grade.upper()
    for company in ("PSA", "BGS", "CGC"):
        if company in upper:
            match = re.search(r"\b(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5)\b", upper)
            return f"{company} {match.group(1)}" if match else company
    return upper


_TCG_GRADE_KIND = {
    "C": "tcg-common",
    "U": "tcg-common",
    "R": "tcg-uncommon",
    "RR": "tcg-uncommon",
    "AR": "tcg-rare",
    "SR": "tcg-rare",
    "SAR": "tcg-epic",
    "UR": "tcg-legendary",
    "MUR": "tcg-legendary",
}


def _grader_kind(card: CardIdentity, price: RenaissPrice) -> str:
    raw = " ".join(
        str(value or "")
        for value in [price.grading_company, price.grade_label, card.grade, card.rarity]
    ).upper()
    if "PSA" in raw:
        return "psa"
    if "BGS" in raw or "BECKETT" in raw:
        if "BLACK" in raw or "PRISTINE" in raw or "10" in raw:
            return "bgs-black"
        return "bgs-gold"
    if "CGC" in raw:
        if "PRISTINE" in raw or "PERFECT" in raw:
            return "cgc-pristine"
        return "cgc"
    # 실물 그레이딩이 없으면 뽑은 카드의 실제 TCG 등급(C~MUR)으로 화려함을 결정한다.
    grade = normalize_grade(card.rarity or card.grade)
    return _TCG_GRADE_KIND.get(grade, "tcg-common")


def _style_vars(kind: str) -> dict[str, str]:
    styles = {
        "psa": {
            "outer": "#b80d18",
            "wrap": "#8B0610",
            "grade_bg": "#F8F5EA",
            "grade_fg": "#8B0610",
            "price_bg": "#F8F5EA",
            "price_fg": "#11110E",
        },
        "bgs-black": {
            "outer": "#42351B",
            "wrap": "#D9B44A",
            "grade_bg": "#F8F5EA",
            "grade_fg": "#9A7217",
            "price_bg": "#F8F5EA",
            "price_fg": "#11110E",
        },
        "bgs-gold": {
            "outer": "#8A6A24",
            "wrap": "#6E551C",
            "grade_bg": "#F8F5EA",
            "grade_fg": "#795719",
            "price_bg": "#F8F5EA",
            "price_fg": "#11110E",
        },
        "cgc-pristine": {
            "outer": "#0B7F88",
            "wrap": "#0B7F88",
            "grade_bg": "#F8F5EA",
            "grade_fg": "#0B7F88",
            "price_bg": "#F8F5EA",
            "price_fg": "#11110E",
        },
        "cgc": {
            "outer": "#0B7F88",
            "wrap": "#0B7F88",
            "grade_bg": "#F8F5EA",
            "grade_fg": "#0B7F88",
            "price_bg": "#F8F5EA",
            "price_fg": "#11110E",
        },
        "raw": {
            "outer": "#846B35",
            "wrap": "#9D7B34",
            "grade_bg": "#F8F5EA",
            "grade_fg": "#6E551C",
            "price_bg": "#F8F5EA",
            "price_fg": "#11110E",
        },
        # ── TCG 등급(가챠 결과) 스타일 — 등급이 높을수록 화려하게 ──
        "tcg-common": {
            "outer": "#6B6B63",
            "wrap": "#8A8A80",
            "grade_bg": "#F1F0EC",
            "grade_fg": "#55554D",
            "price_bg": "#F1F0EC",
            "price_fg": "#11110E",
            "body_bg": "#faf9f6",
            "glow": "none",
        },
        "tcg-uncommon": {
            "outer": "#2E6B4F",
            "wrap": "#3C8563",
            "grade_bg": "#EFF6F1",
            "grade_fg": "#215039",
            "price_bg": "#EFF6F1",
            "price_fg": "#11110E",
            "body_bg": "#f6faf7",
            "glow": "none",
        },
        "tcg-rare": {
            "outer": "#1E4FA0",
            "wrap": "#2E68C7",
            "grade_bg": "#EAF1FC",
            "grade_fg": "#173B78",
            "price_bg": "#EAF1FC",
            "price_fg": "#11110E",
            "body_bg": "#f3f7fd",
            "glow": "0 0 60px rgba(46,104,199,.25)",
        },
        "tcg-epic": {
            "outer": "#7A2E9E",
            "wrap": "#9B3FC9",
            "grade_bg": "#F6EEFB",
            "grade_fg": "#5C2178",
            "price_bg": "#F6EEFB",
            "price_fg": "#11110E",
            "body_bg": "#faf4fd",
            "glow": "0 0 80px rgba(155,63,201,.35)",
        },
        "tcg-legendary": {
            "outer": "#C9971F",
            "wrap": "#E8B923",
            "grade_bg": "#FFF7E0",
            "grade_fg": "#8A5B00",
            "price_bg": "#FFF7E0",
            "price_fg": "#11110E",
            "body_bg": "#fffaf0",
            "glow": "0 0 120px rgba(232,185,35,.45)",
        },
    }
    return styles.get(kind, styles["raw"])


def _render_html(card: dict[str, Any], price: RenaissPrice, original_card: CardIdentity) -> str:
    grade = escape(_grade_text(original_card, price))
    price_label = escape(_price_text(price))
    card_image = escape(str(card.get("image_url") or ""))
    kind = _grader_kind(original_card, price)
    style = _style_vars(kind)

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: #efece4;
  font-family: Poppins, Arial, sans-serif;
}}
.stage {{
  width: 1080px;
  height: 1350px;
  padding: 28px 42px;
  background: #efece4;
}}
.label {{
  position: relative;
  height: 150px;
  overflow: hidden;
  border: 4px solid {style["outer"]};
  border-radius: 12px;
  background: #17150f;
  box-shadow: 0 7px 18px rgba(0,0,0,.18);
}}
.label::before {{
  content: "";
  position: absolute;
  inset: 0;
  background:
    linear-gradient(90deg, rgba(245,224,169,.14), rgba(255,255,255,.03) 50%, rgba(80,93,255,.07));
}}
.logo {{
  position: absolute;
  left: 30px;
  top: 25px;
  width: 414px;
  height: 96px;
  color: #f2ead2;
  filter: drop-shadow(0 1px 0 rgba(0,0,0,.25));
}}
.logo svg {{
  display: block;
  width: 100%;
  height: 100%;
}}
.logo-fallback {{
  display: block;
  padding-top: 20px;
  color: #f2ead2;
  font-size: 52px;
  font-weight: 800;
  letter-spacing: -.8px;
}}
.divider {{
  position: absolute;
  left: 462px;
  top: 24px;
  bottom: 24px;
  width: 1px;
  background: rgba(242,234,210,.16);
}}
.joined {{
  position: absolute;
  right: 10px;
  top: 19px;
  width: 526px;
  height: 112px;
  display: grid;
  grid-template-columns: 220px 306px;
  overflow: hidden;
  border: 5px solid {style["wrap"]};
  border-radius: 10px;
  box-shadow: 0 5px 14px rgba(0,0,0,.18), inset 0 1px 0 rgba(255,255,255,.10);
}}
.grade {{
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  background: {style["grade_bg"]};
  color: {style["grade_fg"]};
  font-size: 47px;
  font-weight: 850;
  line-height: 1;
  letter-spacing: -.45px;
  text-shadow: 0 1px 0 rgba(255,255,255,.28);
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,.55),
    inset 0 -1px 0 rgba(0,0,0,.08),
    inset -1px 0 0 rgba(0,0,0,.34);
}}
.price {{
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  background: {style["price_bg"]};
  color: {style["price_fg"]};
  font-family: "JetBrains Mono", Consolas, monospace;
  font-size: 53px;
  font-weight: 900;
  line-height: 1;
  letter-spacing: -1.2px;
  text-shadow: 0 1px 0 rgba(255,255,255,.14);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.58), inset 0 -1px 0 rgba(0,0,0,.10);
}}
.body {{
  height: 1158px;
  margin-top: 14px;
  padding: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  border: 5px solid {style["outer"]};
  border-radius: 12px;
  background: {style.get("body_bg", "#fbfaf5")};
  box-shadow: {style.get("glow", "none")};
}}
.card-image {{
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}}
</style>
</head>
<body>
<div class="stage">
  <div class="label">
    <div class="divider"></div>
    <div class="logo">{_logo_svg()}</div>
    <div class="joined">
      <div class="grade">{grade}</div>
      <div class="price">{price_label}</div>
    </div>
  </div>
  <div class="body">
    <img class="card-image" src="{card_image}" />
  </div>
</div>
</body>
</html>"""


async def render_overlay_card(card: CardIdentity, price: RenaissPrice) -> bytes | None:
    width, height = 1080, 1350
    render_card = _card_payload(card, price)
    # 원격 카드 이미지를 data URI 로 인라인해 CSP/networkidle 타임아웃을 회피.
    inlined = await resolve_image_to_data_uri(render_card.get("image_url"))
    if inlined:
        render_card["image_url"] = inlined
        render_card["display_image_url"] = inlined
        metadata = render_card.get("metadata") or {}
        metadata["display_image_url"] = inlined
        metadata["image_url"] = inlined
        render_card["metadata"] = metadata
    html = _render_html(render_card, price, card)
    return await render_html_to_png(html, width, height)
