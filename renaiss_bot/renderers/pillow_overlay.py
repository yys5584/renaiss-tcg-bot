"""Fast fixed-frame compositor for Renaiss card images."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Mapping

from PIL import Image, ImageDraw, ImageFont, ImageOps

WIDTH = 1080
HEIGHT = 1350
TEMPLATE_VERSION = "slab-land-v3"
_LOGO_PNG_PATH = Path(__file__).resolve().parents[1] / "assets" / "renaiss_logo.png"
_FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"


def _font_candidates(*, mono: bool, bold: bool) -> list[str]:
    # Bundled fonts come first so local previews and the Ubuntu host render
    # pixel-identically; system fonts only cover a missing bundle.
    bundled = _FONTS_DIR / ("DejaVuSansMono-Bold.ttf" if mono else "DejaVuSans-Bold.ttf")
    windows = Path("C:/Windows/Fonts")
    if mono:
        names = ["consolab.ttf", "consola.ttf"]
    elif bold:
        names = ["arialbd.ttf", "segoeuib.ttf"]
    else:
        names = ["arial.ttf", "segoeui.ttf"]
    return [str(bundled)] + [str(windows / name) for name in names] + [
        "DejaVuSansMono-Bold.ttf" if mono else "DejaVuSans-Bold.ttf",
        "DejaVuSans.ttf",
    ]


@lru_cache(maxsize=64)
def _font(size: int, *, mono: bool = False, bold: bool = True):
    for candidate in _font_candidates(mono=mono, bold=bold):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _centered_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    *,
    fill: str,
    start_size: int,
    minimum_size: int = 20,
    mono: bool = False,
) -> None:
    left, top, right, bottom = box
    available_width = max(1, right - left - 18)
    available_height = max(1, bottom - top - 10)
    selected = _font(start_size, mono=mono)
    for size in range(start_size, minimum_size - 1, -2):
        candidate = _font(size, mono=mono)
        bounds = draw.textbbox((0, 0), text, font=candidate)
        if bounds[2] - bounds[0] <= available_width and bounds[3] - bounds[1] <= available_height:
            selected = candidate
            break
    bounds = draw.textbbox((0, 0), text, font=selected)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    x = left + ((right - left - width) / 2) - bounds[0]
    y = top + ((bottom - top - height) / 2) - bounds[1]
    draw.text((x, y), text, font=selected, fill=fill)


@lru_cache(maxsize=1)
def _brand_logo() -> Image.Image | None:
    try:
        with Image.open(_LOGO_PNG_PATH) as source:
            source.load()
            return source.convert("RGBA")
    except OSError:
        return None


@lru_cache(maxsize=16)
def _fixed_frame(kind: str, style_items: tuple[tuple[str, str], ...]) -> Image.Image:
    """Build one immutable frame per visual grade; callers always copy it."""
    style = dict(style_items)
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), "#efece4")
    draw = ImageDraw.Draw(canvas)

    label = (42, 28, 1038, 178)
    draw.rounded_rectangle(label, radius=12, fill="#17150f", outline=style["outer"], width=4)
    draw.line((504, 52, 504, 154), fill="#4a473e", width=2)

    logo = _brand_logo()
    if logo is not None:
        fitted = ImageOps.contain(logo, (410, 104), method=Image.Resampling.LANCZOS)
        canvas.alpha_composite(fitted, (72, 103 - fitted.height // 2))
    else:
        brand_font = _font(68)
        draw.text((72, 63), "renaiss", font=brand_font, fill="#F2EAD2")

    joined = (514, 47, 1028, 159)
    draw.rounded_rectangle(joined, radius=10, fill=style["wrap"], outline=style["wrap"], width=5)
    draw.rectangle((519, 52, 734, 154), fill=style["grade_bg"])
    draw.rectangle((734, 52, 1023, 154), fill=style["price_bg"])
    draw.line((734, 52, 734, 154), fill="#6c6657", width=2)
    draw.rounded_rectangle(joined, radius=10, outline=style["wrap"], width=5)

    body = (42, 192, 1038, 1349)
    draw.rounded_rectangle(
        body,
        radius=12,
        fill=style.get("body_bg", "#fbfaf5"),
        outline=style["outer"],
        width=5,
    )
    return canvas


def _load_card_image(raw: bytes | None) -> Image.Image | None:
    if not raw:
        return None
    try:
        with Image.open(BytesIO(raw)) as source:
            source.load()
            if source.width <= 0 or source.height <= 0:
                return None
            if source.width * source.height > 50_000_000:
                return None
            return source.convert("RGBA")
    except Exception:
        return None


def render_fixed_overlay(
    *,
    card_image: bytes | None,
    kind: str,
    style: Mapping[str, str],
    grade_text: str,
    price_text: str,
    placeholder_text: str | None = None,
) -> bytes:
    """Composite only the variable card and labels onto a cached fixed frame."""
    style_items = tuple(sorted((str(key), str(value)) for key, value in style.items()))
    canvas = _fixed_frame(kind, style_items).copy()
    draw = ImageDraw.Draw(canvas)
    _centered_text(
        draw,
        (519, 52, 734, 154),
        grade_text,
        fill=style["grade_fg"],
        start_size=47,
    )
    _centered_text(
        draw,
        (734, 52, 1023, 154),
        price_text,
        fill=style["price_fg"],
        start_size=53,
        mono=True,
    )

    card = _load_card_image(card_image)
    if card is not None:
        fitted = ImageOps.contain(card, (958, 1118), method=Image.Resampling.LANCZOS)
        x = 540 - (fitted.width // 2)
        y = 771 - (fitted.height // 2)
        canvas.alpha_composite(fitted, (x, y))
    elif placeholder_text:
        # 블라인드 프롬프트: 카드 자리를 등급색 물음표로 채운다.
        _centered_text(
            draw,
            (200, 380, 880, 1160),
            placeholder_text,
            fill=style["grade_fg"],
            start_size=520,
        )

    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG", compress_level=3)
    return output.getvalue()


def clear_frame_cache() -> None:
    _fixed_frame.cache_clear()
    _font.cache_clear()

# ── Renaiss 실사 슬랩 기반 가로형(16:9) 렌더 ──────────────────────────────
LANDSCAPE_WIDTH = 1600
LANDSCAPE_HEIGHT = 900
_SLAB_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "assets" / "renaiss_slab_template.png"
# 빈 슬랩 템플릿(1024x1024)의 픽셀 스캔 실측값
_SLAB_SLOT = (338, 306, 686, 794)
_SLAB_LABEL = (317, 122, 706, 233)
TIER_COLORS = {
    "TOP": "#C9A227",
    "S": "#7C3AED",
    "A": "#2563EB",
    "B": "#059669",
    "C": "#6B7280",
}
_MACHINE_DIR = Path(__file__).resolve().parents[1] / "assets"


def tier_color(tier: str) -> str:
    return TIER_COLORS.get((tier or "").strip().upper(), TIER_COLORS["C"])


@lru_cache(maxsize=4)
def _machine_art(machine: str) -> Image.Image | None:
    try:
        with Image.open(_MACHINE_DIR / f"renaiss_machine_{machine}.jpg") as source:
            source.load()
            return source.convert("RGB")
    except OSError:
        return None


def render_machine_prompt(*, machine: str, tier: str, tier_label: str) -> bytes | None:
    """블라인드 스폰: 티어에 매칭된 Renaiss 가챠 머신 + 티어 배지."""
    art = _machine_art(machine)
    if art is None:
        return None
    canvas = art.copy()
    draw = ImageDraw.Draw(canvas)
    color = tier_color(tier)
    badge_font = _font(56)
    bounds = draw.textbbox((0, 0), tier_label, font=badge_font)
    pad_x, pad_y = 26, 14
    width = bounds[2] - bounds[0] + pad_x * 2
    height = bounds[3] - bounds[1] + pad_y * 2
    x1, y0 = canvas.width - 28, 28
    x0, y1 = x1 - width, y0 + height
    draw.rounded_rectangle((x0, y0, x1, y1), radius=14, fill=color)
    draw.text(((x0 + x1) // 2, (y0 + y1) // 2), tier_label, fill="#ffffff", font=badge_font, anchor="mm")
    output = BytesIO()
    canvas.save(output, format="PNG", compress_level=3)
    return output.getvalue()


@lru_cache(maxsize=1)
def _slab_template() -> Image.Image | None:
    try:
        with Image.open(_SLAB_TEMPLATE_PATH) as source:
            source.load()
            return source.convert("RGB")
    except OSError:
        return None


@lru_cache(maxsize=1)
def _landscape_backdrop() -> Image.Image:
    """어두운 비네트 배경. 블러가 비싸므로 한 번만 만든다."""
    from PIL import ImageFilter

    canvas = Image.new("RGB", (LANDSCAPE_WIDTH, LANDSCAPE_HEIGHT), (12, 12, 13))
    veil = Image.new("L", (LANDSCAPE_WIDTH, LANDSCAPE_HEIGHT), 0)
    ImageDraw.Draw(veil).ellipse(
        (-300, -250, LANDSCAPE_WIDTH + 300, LANDSCAPE_HEIGHT + 250), fill=38
    )
    veil = veil.filter(ImageFilter.GaussianBlur(180))
    return Image.composite(
        Image.new("RGB", (LANDSCAPE_WIDTH, LANDSCAPE_HEIGHT), (26, 26, 28)), canvas, veil
    )


def render_slab_landscape(
    *,
    card_image: bytes | None,
    tier: str,
    name: str,
    set_line: str,
    price_text: str,
    headline: str,
    label_name: str | None = None,
    grading_label: str = "PSA 10 GEM MINT",
) -> bytes:
    """Renaiss 슬랩을 왼쪽에, 큰 타이포를 오른쪽에 두는 16:9 합성."""
    template = _slab_template()
    if template is None:
        # 템플릿 자산이 없으면 기존 고정 프레임 경로로 폴백한다.
        style = {"grade_bg": "#efece4", "grade_fg": "#17150f", "price_bg": "#efece4",
                 "price_fg": "#17150f", "outer": tier_color(tier), "wrap": "#efece4"}
        return render_fixed_overlay(
            card_image=card_image, kind="tcg-common", style=style,
            grade_text=tier, price_text=price_text,
            placeholder_text=None if card_image else "?",
        )
    color = tier_color(tier)
    tier_label = (tier or "C").strip().upper()

    slab = template.copy()
    draw = ImageDraw.Draw(slab)
    draw.rectangle(
        (_SLAB_LABEL[0] + 1, _SLAB_LABEL[1] + 1, _SLAB_LABEL[2] - 1, _SLAB_LABEL[3] - 1),
        outline=color,
        width=6,
    )
    label_center_y = (_SLAB_LABEL[1] + _SLAB_LABEL[3]) // 2
    tier_font = _font(52)
    tier_width = draw.textlength(tier_label, font=tier_font)
    draw.text(
        (_SLAB_LABEL[2] - 26, label_center_y),
        tier_label,
        fill=color,
        font=tier_font,
        anchor="rm",
    )
    # 이름은 티어 배지와 겹치지 않게 남은 폭에 맞춰 폰트를 줄이고,
    # 그 아래 감정등급(PSA 10)을 원본 슬랩 라벨처럼 병기한다.
    label_text = label_name if label_name is not None else name
    available = (_SLAB_LABEL[2] - 26 - tier_width - 18) - (_SLAB_LABEL[0] + 26)
    name_font = _font(38)
    for size in range(38, 19, -2):
        name_font = _font(size)
        if draw.textlength(label_text, font=name_font) <= available:
            break
    else:
        while label_text and draw.textlength(label_text + "…", font=name_font) > available:
            label_text = label_text[:-1]
        label_text += "…"
    draw.text(
        (_SLAB_LABEL[0] + 26, label_center_y - 16),
        label_text,
        fill=(15, 15, 15),
        font=name_font,
        anchor="lm",
    )
    if grading_label:
        draw.text(
            (_SLAB_LABEL[0] + 26, label_center_y + 26),
            grading_label[:22],
            fill=(100, 100, 100),
            font=_font(22),
            anchor="lm",
        )
    card = _load_card_image(card_image)
    if card is not None:
        fitted = ImageOps.contain(
            card,
            (_SLAB_SLOT[2] - _SLAB_SLOT[0], _SLAB_SLOT[3] - _SLAB_SLOT[1]),
            method=Image.Resampling.LANCZOS,
        )
        slab.paste(
            fitted,
            (
                (_SLAB_SLOT[0] + _SLAB_SLOT[2]) // 2 - fitted.width // 2,
                (_SLAB_SLOT[1] + _SLAB_SLOT[3]) // 2 - fitted.height // 2,
            ),
            fitted if fitted.mode == "RGBA" else None,
        )
    else:
        draw.text(
            ((_SLAB_SLOT[0] + _SLAB_SLOT[2]) // 2, (_SLAB_SLOT[1] + _SLAB_SLOT[3]) // 2 - 10),
            "?",
            fill=color,
            font=_font(300),
            anchor="mm",
        )

    canvas = _landscape_backdrop().copy()
    draw = ImageDraw.Draw(canvas)
    slab_fit = ImageOps.contain(
        slab.crop((250, 60, 775, 1000)), (620, LANDSCAPE_HEIGHT - 80),
        method=Image.Resampling.LANCZOS,
    )
    canvas.paste(slab_fit, (70, (LANDSCAPE_HEIGHT - slab_fit.height) // 2))

    text_x = 760
    draw.text((text_x, 190), headline[:26], fill=(150, 150, 155), font=_font(44))
    # 캔버스 우측 여백(90px)을 넘지 않게 각 줄의 폰트를 자동 축소한다.
    text_area = LANDSCAPE_WIDTH - text_x - 90

    def _fit(text: str, start: int, minimum: int, *, bold: bool = True):
        chosen = _font(start, bold=bold)
        for size in range(start, minimum - 1, -4):
            chosen = _font(size, bold=bold)
            if draw.textlength(text, font=chosen) <= text_area:
                break
        return chosen

    headline_font = _fit(name[:26], 84, 44)
    draw.text((text_x, 262), name[:26], fill=(240, 240, 238), font=headline_font)
    set_font = _fit(set_line[:58], 36, 22, bold=False)
    draw.text((text_x, 396), set_line[:58], fill=(150, 150, 155), font=set_font)

    badge_font = _font(64)
    bounds = draw.textbbox((0, 0), tier_label, font=badge_font)
    badge_w = bounds[2] - bounds[0] + 68
    badge = (text_x, 490, text_x + badge_w, 586)
    draw.rounded_rectangle(badge, radius=16, fill=color)
    draw.text(
        ((badge[0] + badge[2]) // 2, (badge[1] + badge[3]) // 2),
        tier_label, fill=(255, 255, 255), font=badge_font, anchor="mm",
    )
    price_font = _font(88 if len(price_text) <= 8 else 54)
    draw.text(
        (badge[2] + 40, (badge[1] + badge[3]) // 2),
        price_text[:14], fill=(240, 240, 238), font=price_font, anchor="lm",
    )

    logo = _brand_logo()
    if logo is not None:
        fitted_logo = logo.resize((300, int(logo.height * 300 / logo.width)))
        canvas.paste(
            fitted_logo,
            (LANDSCAPE_WIDTH - 300 - 70, LANDSCAPE_HEIGHT - fitted_logo.height - 56),
            fitted_logo,
        )

    output = BytesIO()
    canvas.save(output, format="PNG", compress_level=3)
    return output.getvalue()

