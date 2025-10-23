"""Detector for proxy authentication (HTTP 407) pages."""

from __future__ import annotations

from logging import Logger, getLogger
from typing import Final, Optional

from playwright.async_api import Error as PlaywrightError, Page, Response

__all__ = [
    "DETECTOR_ID",
    "CARD_SELECTOR",
    "proxy_auth_407_detector",
]

DETECTOR_ID: Final[str] = "proxy_auth_407_detector"
CARD_SELECTOR: Final[str] = 'span[data-marker="item-view/item-id"]'


async def proxy_auth_407_detector(
    page: Page,
    *,
    last_response: Optional[Response] = None,
    logger: Optional[Logger] = None,
) -> str | bool:
    """Returns `proxy_auth_407_detector` when the page requires proxy credentials."""

    log = logger or getLogger(__name__)
    status = getattr(last_response, "status", None)

    if status != 407:
        return False

    try:
        card_present = await page.locator(CARD_SELECTOR).count() > 0
    except PlaywrightError:
        card_present = False

    if card_present:
        return False

    log.info("Detected proxy authentication requirement (status 407).")
    return DETECTOR_ID
