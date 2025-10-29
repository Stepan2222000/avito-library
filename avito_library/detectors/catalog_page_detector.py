"""Detector for identifying Avito catalog pages instead of single listings."""

from __future__ import annotations

from logging import Logger, getLogger
from typing import Final, Optional

from playwright.async_api import Error as PlaywrightError, Page

from ..debug import DEBUG_SCREENSHOTS, capture_debug_screenshot

__all__ = [
    "DETECTOR_ID",
    "CatalogEmptyError",
    "CATALOG_CONTAINER_SELECTOR",
    "CATALOG_ITEM_SELECTOR",
    "CARD_SELECTOR",
    "catalog_page_detector",
]

DETECTOR_ID: Final[str] = "catalog_page_detector"
CATALOG_CONTAINER_SELECTOR: Final[str] = 'div[data-marker="catalog-serp"]'
CATALOG_ITEM_SELECTOR: Final[str] = 'div[data-marker="item"]'
CARD_SELECTOR: Final[str] = 'span[data-marker="item-view/item-id"]'


class CatalogEmptyError(RuntimeError):
    """Raised when catalog container exists but has no items."""


async def catalog_page_detector(
    page: Page,
    *,
    logger: Optional[Logger] = None,
) -> str | bool:
    """Detects catalog pages when the catalog container is present without a card."""

    log = logger or getLogger(__name__)

    try:
        has_card = await _has_selector(page, CARD_SELECTOR)
        if has_card:
            return False

        has_catalog_container = await _has_selector(page, CATALOG_CONTAINER_SELECTOR)
        if not has_catalog_container:
            return False

        container_locator = page.locator(CATALOG_CONTAINER_SELECTOR)
        items_count = await container_locator.locator(CATALOG_ITEM_SELECTOR).count()
    except PlaywrightError:
        return False

    if items_count == 0:
        raise CatalogEmptyError(
            "Catalog detected but no items found inside the container.",
        )

    log.info(
        "Catalog page detected: container=%s items=%d",
        CATALOG_CONTAINER_SELECTOR,
        items_count,
    )
    await capture_debug_screenshot(
        page,
        enabled=DEBUG_SCREENSHOTS,
        label=f"detector-catalog-{items_count}",
    )
    return DETECTOR_ID


async def _has_selector(page: Page, selector: str) -> bool:
    """Checks whether the selector exists on the page (presence only)."""

    try:
        return await page.locator(selector).count() > 0
    except PlaywrightError:
        return False
