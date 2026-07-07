"""Self-contained Playwright HTML→PNG renderer for the Renaiss bot.

Standalone: no dependency on the main TGPoke repo. Keeps one Chromium process
alive and renders card images from HTML. Remote images are inlined as data URIs
so they survive strict CSP / networkidle timeouts.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os

import aiohttp

logger = logging.getLogger(__name__)


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
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
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
    """Fetch a remote http(s) image and return a data: URI. data: URIs pass through.
    On any failure returns the original url (browser can still try to load it)."""
    if not url:
        return url
    if url.startswith("data:"):
        return url
    if not url.startswith(("http://", "https://")):
        return url
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return url
                content_type = response.headers.get("Content-Type", "image/png").split(";")[0].strip()
                if not content_type.startswith("image/"):
                    content_type = "image/png"
                raw = await response.read()
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{content_type};base64,{encoded}"
    except Exception as exc:
        logger.debug("Renaiss image inline skipped (%s): %s", url, exc)
        return url


async def render_html_to_png(html: str, width: int, height: int) -> bytes:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    await _ensure_browser()
    if _render_sem is None:
        raise RuntimeError("Renaiss renderer semaphore is not initialized")

    await _render_sem.acquire()
    page = None
    try:
        page = await _browser.new_page(viewport={"width": width, "height": height})
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
