"""Detector for Avito seller profile pages."""

from __future__ import annotations

from typing import Final

from playwright.async_api import Error as PlaywrightError, Page

from ..debug import DEBUG_SCREENSHOTS, capture_debug_screenshot

__all__ = [
    "DETECTOR_ID",
    "TABS_SELECTOR",
    "PROFILE_CONTAINER_SELECTOR",
    "PROFILE_SCORE_SELECTOR",
    "NAME_SELECTOR",
    "seller_profile_detector",
]

DETECTOR_ID: Final[str] = "seller_profile_detector"
TABS_SELECTOR: Final[str] = 'div[data-marker="extended_profile_tabs"]'
PROFILE_CONTAINER_SELECTOR: Final[str] = 'div[data-marker="profile"]'
PROFILE_SCORE_SELECTOR: Final[str] = (
    '[data-marker="profile/summary"]'
)
NAME_SELECTOR: Final[str] = 'h1[data-marker^="name "]'


async def seller_profile_detector(page: Page) -> str | bool:
    """Returns `seller_profile_detector` when seller profile markers are present."""

    try:
        tabs_present = await page.locator(TABS_SELECTOR).count() > 0
        if not tabs_present:
            return False

        profile_present = await page.locator(PROFILE_CONTAINER_SELECTOR).count() > 0
        if not profile_present:
            return False

        # Score/summary block might render either as summary link or score span across layouts.
        score_present = await page.locator(PROFILE_SCORE_SELECTOR).count() > 0
        if not score_present:
            return False

        name_locator = page.locator(NAME_SELECTOR)
        if await name_locator.count() == 0:
            return False
        name_text = (await name_locator.first.text_content()) or ""
    except PlaywrightError:
        return False

    if name_text.strip():
        await capture_debug_screenshot(
            page,
            enabled=DEBUG_SCREENSHOTS,
            label="detector-seller-profile",
        )
        return DETECTOR_ID
    return False
