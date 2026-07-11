"""Fast fixed-frame compositor for Renaiss card images."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Mapping

from PIL import Image, ImageDraw, ImageFont, ImageOps

WIDTH = 1080
HEIGHT = 1350
TEMPLATE_VERSION = "pillow-v1"


def _font_candidates(*, mono: bool, bold: bool) -> list[str]:
    windows = Path("C:/Windows/Fonts")
    if mono:
        names = ["consolab.ttf", "consola.ttf"]
    elif bold:
        names = ["arialbd.ttf", "segoeuib.ttf"]
    else:
        names = ["arial.ttf", "segoeui.ttf"]
    return [str(windows / name) for name in names] + [
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


@lru_cache(maxsize=16)
def _fixed_frame(kind: str, style_items: tuple[tuple[str, str], ...]) -> Image.Image:
    """Build one immutable frame per visual grade; callers always copy it."""
    style = dict(style_items)
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), "#efece4")
    draw = ImageDraw.Draw(canvas)

    label = (42, 28, 1038, 178)
    draw.rounded_rectangle(label, radius=12, fill="#17150f", outline=style["outer"], width=4)
    draw.line((504, 52, 504, 154), fill="#4a473e", width=2)

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

    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG", compress_level=3)
    return output.getvalue()


def clear_frame_cache() -> None:
    _fixed_frame.cache_clear()
    _font.cache_clear()
