"""Remote card images cannot turn the renderer into an SSRF client."""

from __future__ import annotations

import base64
import asyncio
from io import BytesIO
from unittest.mock import AsyncMock

from PIL import Image

from renaiss_bot.renderers.overlay import clear_overlay_caches, render_overlay_card
from renaiss_bot.renderers.playwright_render import (
    _remote_image_url_allowed,
    resolve_image_to_data_uri,
)
from renaiss_bot.services.models import CardIdentity, RenaissPrice


def test_renderer_url_gate_requires_https_allowlist_and_standard_port(monkeypatch):
    monkeypatch.setenv("RENAISS_IMAGE_ALLOWED_HOSTS", "images.example.com")

    assert _remote_image_url_allowed("https://images.example.com/card.png")
    assert not _remote_image_url_allowed("http://images.example.com/card.png")
    assert not _remote_image_url_allowed("https://images.example.com:444/card.png")
    assert not _remote_image_url_allowed("https://user:secret@images.example.com/card.png")
    assert not _remote_image_url_allowed("https://127.0.0.1/card.png")


async def test_renderer_rejects_unapproved_url_without_network(monkeypatch):
    monkeypatch.setenv("RENAISS_IMAGE_ALLOWED_HOSTS", "images.example.com")

    assert await resolve_image_to_data_uri("http://127.0.0.1:8080/private") is None
    assert await resolve_image_to_data_uri("https://evil.example/card.png") is None


async def test_renderer_accepts_only_bounded_raster_data_uris(monkeypatch):
    raw = b"\x89PNG\r\n\x1a\nsmall-png-placeholder"
    valid = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    svg = "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode("ascii")
    mislabeled = "data:image/png;base64," + base64.b64encode(b"<svg/>").decode("ascii")
    monkeypatch.setenv("RENAISS_IMAGE_MAX_BYTES", "64000")

    assert await resolve_image_to_data_uri(valid) == valid
    assert await resolve_image_to_data_uri(svg) is None
    assert await resolve_image_to_data_uri(mislabeled) is None
    assert await resolve_image_to_data_uri("data:image/png,not-base64") is None


async def test_rejected_image_source_never_reaches_the_fixed_frame_compositor(monkeypatch):
    clear_overlay_caches()
    capture = AsyncMock(return_value=b"png")
    monkeypatch.setattr(
        "renaiss_bot.renderers.overlay.resolve_image_to_data_uri",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("renaiss_bot.renderers.overlay.render_html_to_png", capture)

    result = await render_overlay_card(
        CardIdentity(
            category="pokemon_tcg",
            card_name="Unsafe Card",
            image_url="file:///C:/Windows/win.ini",
        ),
        RenaissPrice(status="missing", source="test"),
    )

    assert result.startswith(b"\x89PNG\r\n\x1a\n")
    capture.assert_not_awaited()


async def test_oversized_data_uri_is_rejected_by_decoded_size(monkeypatch):
    monkeypatch.setenv("RENAISS_IMAGE_MAX_BYTES", "64000")
    oversized = b"\x89PNG\r\n\x1a\n" + (b"x" * 64_000)
    encoded = "data:image/png;base64," + base64.b64encode(oversized).decode("ascii")

    assert await resolve_image_to_data_uri(encoded) is None


async def test_overlay_gate_bounds_and_deduplicates_image_downloads_without_chromium(monkeypatch):
    clear_overlay_caches()
    release = asyncio.Event()

    async def delayed_inline(_url):
        await release.wait()
        return None

    resolver = AsyncMock(side_effect=delayed_inline)
    monkeypatch.setenv("RENAISS_RENDER_POOL_SIZE", "1")
    monkeypatch.setenv("RENAISS_RENDER_TOTAL_TIMEOUT_SECONDS", "5")
    monkeypatch.setattr(
        "renaiss_bot.renderers.overlay.resolve_image_to_data_uri",
        resolver,
    )
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Bounded Card",
        image_url="https://images.example.com/card.png",
    )
    price = RenaissPrice(status="missing", source="test")

    first = asyncio.create_task(render_overlay_card(card, price))
    second = asyncio.create_task(render_overlay_card(card, price))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert resolver.await_count == 1
    release.set()
    first_png = await first
    second_png = await second
    assert first_png.startswith(b"\x89PNG\r\n\x1a\n")
    assert second_png == first_png


async def test_fixed_frame_places_the_card_without_starting_chromium(monkeypatch):
    clear_overlay_caches()
    source = BytesIO()
    Image.new("RGB", (400, 600), "#e02020").save(source, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(source.getvalue()).decode("ascii")
    chromium = AsyncMock(side_effect=AssertionError("Chromium must not run"))
    monkeypatch.setattr("renaiss_bot.renderers.overlay.render_html_to_png", chromium)

    result = await render_overlay_card(
        CardIdentity(
            category="pokemon_tcg",
            card_name="Fast Card",
            grade="R",
            image_url=data_uri,
        ),
        RenaissPrice(status="missing", source="test"),
    )

    with Image.open(BytesIO(result)) as rendered:
        assert rendered.size == (1600, 900)  # landscape slab layout
        # landscape layout: the card sits inside the slab on the left panel
        red, green, blue = rendered.convert("RGB").getpixel((298, 467))
        assert red > 180 and green < 80 and blue < 80
    chromium.assert_not_awaited()
