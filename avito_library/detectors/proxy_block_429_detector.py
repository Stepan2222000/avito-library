"""Detector for proxy throttling (HTTP 429) pages."""

from __future__ import annotations

import asyncio
import time
from typing import Final, Optional

from playwright.async_api import Error as PlaywrightError, Page, Response

from debug import DEBUG_SCREENSHOTS, capture_debug_screenshot

__all__ = [
    "DETECTOR_ID",
    "CARD_SELECTOR",
    "BUTTON_SELECTOR",
    "REQUIRED_PHRASES",
    "proxy_block_429_detector",
]

DETECTOR_ID: Final[str] = "proxy_block_429_detector"
CARD_SELECTOR: Final[str] = 'span[data-marker="item-view/item-id"]'
BUTTON_SELECTOR: Final[str] = 'button[name="submit"]'
REQUIRED_PHRASES: Final[tuple[str, ...]] = (
    "доступ ограничен:",
    "проблема с ip",
)
WAIT_TIMEOUT_SECONDS: Final[float] = 10.0
POLL_INTERVAL_SECONDS: Final[float] = 0.5


async def proxy_block_429_detector(
    page: Page,
    *,
    last_response: Optional[Response] = None,
) -> str | bool:
    """Returns `proxy_block_429_detector` when retry limits indicate a hard block."""


    


    status = getattr(last_response, "status", None)
    if status != 429:
        return False

    if await _has_selector(page, CARD_SELECTOR):
        return False

    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    while time.monotonic() <= deadline:
        has_button = await _has_selector(page, BUTTON_SELECTOR)
        html = await _safe_page_content(page)
        if has_button and html and _contains_all_phrases(html, REQUIRED_PHRASES):
            await capture_debug_screenshot(
                page,
                enabled=DEBUG_SCREENSHOTS,
                label="detector-proxy-429",
            )
            return DETECTOR_ID
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    return False


def _contains_all_phrases(html: str, phrases: tuple[str, ...]) -> bool:
    lowered = html.lower()
    return all(phrase in lowered for phrase in phrases)


async def _has_selector(page: Page, selector: str) -> bool:
    try:
        return await page.locator(selector).count() > 0
    except PlaywrightError:
        return False


async def _safe_page_content(page: Page) -> Optional[str]:
    try:
        return await page.content()
    except PlaywrightError:
        return None
