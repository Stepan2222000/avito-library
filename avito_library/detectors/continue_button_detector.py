"""Detector for continue button presence."""

from __future__ import annotations

import asyncio
from typing import Final

from playwright.async_api import Error as PlaywrightError, Page

from debug import DEBUG_SCREENSHOTS, capture_debug_screenshot

__all__ = ["DETECTOR_ID", "BUTTON_SELECTOR", "continue_button_detector"]

DETECTOR_ID: Final[str] = "continue_button_detector"
BUTTON_SELECTOR: Final[str] = 'button[name="submit"]'


async def continue_button_detector(page: Page) -> str | bool:
    deadline = asyncio.get_running_loop().time() + 5.0
    try:
        while asyncio.get_running_loop().time() < deadline:
            button = page.locator(BUTTON_SELECTOR)
            if await button.count() == 0:
                await asyncio.sleep(0.2)
                continue
            if await button.first.is_visible():
                await capture_debug_screenshot(
                    page,
                    enabled=DEBUG_SCREENSHOTS,
                    label="detector-continue-button",
                )
                return DETECTOR_ID
            await asyncio.sleep(0.2)
    except PlaywrightError:
        return False

    return False
