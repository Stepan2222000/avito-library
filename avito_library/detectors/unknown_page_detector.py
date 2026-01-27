"""Detector for known edge case pages that don't fit other categories.

Catches pages like "Журнал Авито Авто" (editorial/blog pages) that are not
catalog, card, or other known page types.
"""

from __future__ import annotations

from logging import Logger, getLogger
from typing import Final, Optional

from playwright.async_api import Error as PlaywrightError, Page

from ..debug import DEBUG_SCREENSHOTS, capture_debug_screenshot

__all__ = ["DETECTOR_ID", "unknown_page_detector"]

DETECTOR_ID: Final[str] = "unknown_page_detector"

# Selectors that must be ABSENT for detection
CATALOG_SELECTOR: Final[str] = 'div[data-marker="catalog-serp"]'
CARD_SELECTOR: Final[str] = 'span[data-marker="item-view/item-id"]'

# Phrases that indicate known edge cases (case-insensitive)
EDGE_CASE_PHRASES: Final[tuple[str, ...]] = (
    "журнал",
)


async def _has_selector(page: Page, selector: str) -> bool:
    """Check if selector exists on page."""
    try:
        locator = page.locator(selector)
        return await locator.count() > 0
    except PlaywrightError:
        return False


async def _safe_page_content(page: Page) -> str:
    """Safely get page HTML content."""
    try:
        return await page.content()
    except PlaywrightError:
        return ""


async def unknown_page_detector(
    page: Page,
    *,
    logger: Optional[Logger] = None,
) -> str | bool:
    """Detects known edge case pages that aren't catalog or card pages.

    Detection logic (all conditions must be true):
    1. No catalog selector (div[data-marker="catalog-serp"])
    2. No card selector (span[data-marker="item-view/item-id"])
    3. Page contains one of the edge case phrases (e.g., "журнал")

    Returns:
        DETECTOR_ID ("unknown_page_detector") if edge case detected, False otherwise.
    """
    log = logger or getLogger(__name__)

    try:
        # Check that catalog selector is absent
        if await _has_selector(page, CATALOG_SELECTOR):
            return False

        # Check that card selector is absent
        if await _has_selector(page, CARD_SELECTOR):
            return False

        # Get page content for phrase matching
        html = await _safe_page_content(page)
        if not html:
            return False

        html_lower = html.lower()

        # Check for edge case phrases
        for phrase in EDGE_CASE_PHRASES:
            if phrase.lower() in html_lower:
                log.info(
                    "Unknown page detected: edge case phrase '%s' found",
                    phrase,
                )
                await capture_debug_screenshot(
                    page,
                    enabled=DEBUG_SCREENSHOTS,
                    label="detector-unknown-page",
                )
                return DETECTOR_ID

    except PlaywrightError as e:
        log.debug("Error in unknown_page_detector: %s", e)
        return False

    return False
