"""Self-contained Playwright HTML→PNG renderer for the Renaiss bot.

Standalone: no dependency on the main TGPoke repo. Keeps one Chromium process
alive and renders card images from HTML. Remote images are inlined as data URIs
so they survive strict CSP / networkidle timeouts.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)
_ALLOWED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/avif",
}


def _max_image_bytes() -> int:
    try:
        return min(10_000_000, max(64_000, int(os.getenv("RENAISS_IMAGE_MAX_BYTES", "5000000"))))
    except ValueError:
        return 5_000_000


def _configured_image_hosts() -> set[str]:
    return {
        item.strip().lower().rstrip(".")
        for item in os.getenv(
            "RENAISS_IMAGE_ALLOWED_HOSTS",
            "images.pokemontcg.io",
        ).split(",")
        if item.strip()
    }


def _remote_image_url_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.hostname.lower().rstrip(".") in _configured_image_hosts()
        and parsed.port in {None, 443}
        and not parsed.username
        and not parsed.password
    )


async def _host_resolves_public(hostname: str) -> bool:
    try:
        records = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            443,
            type=socket.SOCK_STREAM,
        )
    except (OSError, socket.gaierror):
        return False
    if not records:
        return False
    for record in records:
        try:
            address = ipaddress.ip_address(record[4][0])
        except ValueError:
            return False
        if not address.is_global:
            return False
    return True


def _safe_data_uri(url: str) -> str | None:
    # Base64 expands bytes by roughly 4/3. Keep a cheap encoded-size guard,
    # then validate and measure the decoded payload so this path has the same
    # byte ceiling as remote downloads.
    if len(url) > ((_max_image_bytes() * 4 // 3) + 256):
        return None
    header, separator, payload = url.partition(",")
    if not separator or ";base64" not in header.lower():
        return None
    content_type = header[5:].split(";", 1)[0].lower()
    if content_type not in _ALLOWED_IMAGE_TYPES or not payload:
        return None
    try:
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError):
        return None
    if not raw or len(raw) > _max_image_bytes():
        return None
    return url if _image_bytes_match_type(raw, content_type) else None


def _image_bytes_match_type(raw: bytes, content_type: str) -> bool:
    """Reject mislabeled HTML/SVG before it reaches Chromium's decoder."""
    if content_type == "image/png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/jpeg":
        return raw.startswith(b"\xff\xd8\xff")
    if content_type == "image/gif":
        return raw.startswith((b"GIF87a", b"GIF89a"))
    if content_type == "image/webp":
        return len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
    if content_type == "image/avif":
        return len(raw) >= 12 and raw[4:8] == b"ftyp" and raw[8:12] in {
            b"avif",
            b"avis",
        }
    return False


def _render_pool_size() -> int:
    try:
        return max(1, int(os.getenv("RENAISS_RENDER_POOL_SIZE", "2")))
    except ValueError:
        return 2


_playwright = None
_browser = None
_browser_lock = asyncio.Lock()
_render_sem: asyncio.Semaphore | None = None


async def _ensure_browser() -> None:
    global _playwright, _browser, _render_sem
    try:
        if _browser is not None and _browser.is_connected():
            if _render_sem is None:
                _render_sem = asyncio.Semaphore(_render_pool_size())
            return
    except Exception:
        pass

    async with _browser_lock:
        try:
            if _browser is not None and _browser.is_connected():
                if _render_sem is None:
                    _render_sem = asyncio.Semaphore(_render_pool_size())
                return
        except Exception:
            pass

        from playwright.async_api import async_playwright

        if _playwright is not None:
            try:
                await _playwright.stop()
            except Exception:
                pass
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            # Keep Chromium's process sandbox enabled. Callers already fall
            # back to text when rendering fails, so availability must never
            # weaken this boundary.
            args=["--disable-gpu", "--disable-dev-shm-usage"],
        )
        _render_sem = asyncio.Semaphore(_render_pool_size())
        logger.info("Renaiss renderer browser ready (pool=%s)", _render_pool_size())


async def _reset_browser() -> None:
    global _browser
    try:
        if _browser is not None:
            await _browser.close()
    except Exception:
        pass
    _browser = None


async def close_renderer() -> None:
    """Shutdown hook: release Playwright resources."""
    global _playwright, _browser, _render_sem
    try:
        if _browser is not None:
            await asyncio.wait_for(_browser.close(), timeout=3.0)
    except Exception as exc:
        logger.debug("Renaiss browser close skipped: %s", exc)
    _browser = None
    _render_sem = None
    try:
        if _playwright is not None:
            await asyncio.wait_for(_playwright.stop(), timeout=3.0)
    except Exception as exc:
        logger.debug("Renaiss playwright stop skipped: %s", exc)
    _playwright = None


async def resolve_image_to_data_uri(url: str | None, *, timeout_seconds: float = 6.0) -> str | None:
    """Inline one allowlisted public image; never let Chromium fetch remote URLs."""
    if not url:
        return None
    if url.startswith("data:"):
        return _safe_data_uri(url)
    if not _remote_image_url_allowed(url):
        return None
    parsed = urlparse(url)
    if not parsed.hostname or not await _host_resolves_public(parsed.hostname):
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    return None
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if content_type not in _ALLOWED_IMAGE_TYPES:
                    return None
                declared_length = response.content_length
                if declared_length is not None and declared_length > _max_image_bytes():
                    return None
                chunks = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    chunks.extend(chunk)
                    if len(chunks) > _max_image_bytes():
                        return None
                raw = bytes(chunks)
                if not raw or not _image_bytes_match_type(raw, content_type):
                    return None
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{content_type};base64,{encoded}"
    except Exception as exc:
        logger.debug("Renaiss image inline skipped (%s): %s", url, exc)
        return None


async def render_html_to_png(html: str, width: int, height: int) -> bytes:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    await _ensure_browser()
    if _render_sem is None:
        raise RuntimeError("Renaiss renderer semaphore is not initialized")

    await _render_sem.acquire()
    page = None
    try:
        page = await _browser.new_page(viewport={"width": width, "height": height})
        async def block_remote(route):
            scheme = urlparse(route.request.url).scheme.lower()
            if scheme in {"about", "data"}:
                await route.continue_()
            else:
                await route.abort()

        await page.route("**/*", block_remote)
        await page.set_content(html, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=1500)
        except PlaywrightTimeoutError:
            logger.info("Renaiss render: remote image wait timed out, capturing current state")
        try:
            await page.wait_for_function(
                """
                () => Array.from(document.images).every((img) =>
                    img.complete && img.naturalWidth > 0 && img.naturalHeight > 0
                )
                """,
                timeout=1500,
            )
        except PlaywrightTimeoutError:
            logger.warning("Renaiss render: image decode wait timed out, capturing current state")
        return await page.screenshot(
            full_page=False,
            clip={"x": 0, "y": 0, "width": width, "height": height},
        )
    except Exception:
        await _reset_browser()
        raise
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception as exc:
                logger.debug("Renaiss render page close skipped: %s", exc)
        _render_sem.release()
