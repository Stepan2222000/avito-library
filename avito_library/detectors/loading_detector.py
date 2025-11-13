"""Detector for identifying page loading state (spinner visible)."""

from __future__ import annotations

from logging import Logger, getLogger
from typing import Final, Optional

from playwright.async_api import Error as PlaywrightError, Page

from ..debug import DEBUG_SCREENSHOTS, capture_debug_screenshot

__all__ = ["DETECTOR_ID", "LOADING_SELECTORS", "loading_detector"]

DETECTOR_ID: Final[str] = "loading_detector"

# Multiple selectors to detect loading spinners
LOADING_SELECTORS: Final[tuple[str, ...]] = (
    'div[class*="loader"]',  # Main loader container (most common)
    'div[class*="spinner"]',  # Alternative spinner class
    'div[class*="loading"]',  # Alternative loading class
)


async def loading_detector(
    page: Page,
    *,
    logger: Optional[Logger] = None,
) -> str | bool:
    """Returns `loading_detector` when a loading spinner is visible on the page."""

    log = logger or getLogger(__name__)

    try:
        for selector in LOADING_SELECTORS:
            locator = page.locator(selector)
            count = await locator.count()

            if count > 0:
                # Check if at least one loader is visible
                for i in range(count):
                    element = locator.nth(i)
                    if await element.is_visible():
                        log.info("Loading spinner detected via selector: %s", selector)
                        await capture_debug_screenshot(
                            page,
                            enabled=DEBUG_SCREENSHOTS,
                            label="detector-loading",
                        )
                        return DETECTOR_ID

    except PlaywrightError as e:
        log.debug("PlaywrightError in loading_detector: %s", e)
        return False

    return False
