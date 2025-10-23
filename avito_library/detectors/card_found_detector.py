"""Detector that confirms Avito listing card is visible."""

from __future__ import annotations

from logging import Logger, getLogger
from typing import Final, Optional

from playwright.async_api import Error as PlaywrightError, Page

__all__ = ["DETECTOR_ID", "SELECTOR", "card_found_detector"]

DETECTOR_ID: Final[str] = "card_found_detector"
SELECTOR: Final[str] = 'span[data-marker="item-view/item-id"]'


async def card_found_detector(
    page: Page,
    *,
    logger: Optional[Logger] = None,
) -> str | bool:
    """Returns `card_found_detector` when the card id span is present and visible."""

    log = logger or getLogger(__name__)
    try:
        locator = page.locator(SELECTOR)
        if await locator.count() == 0:
            return False
        if await locator.first.is_visible():
            log.info("Listing card detected via selector %s", SELECTOR)
            return DETECTOR_ID
    except PlaywrightError:
        return False

    return False
