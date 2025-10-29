"""Handler for Avito continue button and state detection."""

from __future__ import annotations

import asyncio
import time
from typing import Mapping, Optional

from playwright.async_api import Page

from ..debug import DEBUG_SCREENSHOTS, capture_debug_screenshot

from ..detectors import (
    detect_page_state,
    CAPTCHA_DETECTOR_ID,
    CARD_FOUND_DETECTOR_ID,
    CATALOG_DETECTOR_ID,
    CONTINUE_BUTTON_DETECTOR_ID,
    PROXY_AUTH_DETECTOR_ID,
    PROXY_BLOCK_403_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
    REMOVED_DETECTOR_ID,
    SELLER_PROFILE_DETECTOR_ID,
)
_CONTINUE_STATE = CONTINUE_BUTTON_DETECTOR_ID
_CAPTCHA_STATE = CAPTCHA_DETECTOR_ID
_PRIORITY_ORDER = (
    PROXY_BLOCK_403_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
    PROXY_AUTH_DETECTOR_ID,
    REMOVED_DETECTOR_ID,
    SELLER_PROFILE_DETECTOR_ID,
    CATALOG_DETECTOR_ID,
    CARD_FOUND_DETECTOR_ID,
    _CAPTCHA_STATE,
    _CONTINUE_STATE,
)

__all__ = ["press_continue_and_detect"]


async def press_continue_and_detect(
    page: Page,
    *,
    skip_initial_detector: bool = False,
    detector_kwargs: Optional[Mapping[str, Mapping[str, object]]] = None,
    max_retries: int = 10,
    wait_timeout: float = 30.0,
) -> str:
    """Press "Continue" button and detect resulting page state."""

    if not skip_initial_detector:
        initial_state = await detect_page_state(
            page,
            priority=_PRIORITY_ORDER,
            detector_kwargs=detector_kwargs,
        )

        if initial_state != _CONTINUE_STATE:
            await capture_debug_screenshot(
                page,
                enabled=DEBUG_SCREENSHOTS,
                label=f"continue-initial-{initial_state}",
            )
            return initial_state

    button = page.locator('button[name="submit"]')
    for i in range(5):
        await button.click(force=True)


    attempts = 0
    deadline = time.monotonic() + wait_timeout

    while time.monotonic() < deadline:
        await asyncio.sleep(10)
        state = await detect_page_state(
            page,
            priority=_PRIORITY_ORDER,
            detector_kwargs=detector_kwargs,
        )
        print(state)
        if state == _CAPTCHA_STATE:
            await capture_debug_screenshot(
                page,
                enabled=DEBUG_SCREENSHOTS,
                label="continue-captcha",
            )
            return state
        if state == _CONTINUE_STATE:
            if attempts >= max_retries:
                break
            print("выполни клики")
            for i in range(5):
                await button.click(force=True)
            attempts += 1
            await capture_debug_screenshot(
                page,
                enabled=DEBUG_SCREENSHOTS,
                label=f"continue-repeat-{attempts}",
            )
            continue
        await capture_debug_screenshot(
            page,
            enabled=DEBUG_SCREENSHOTS,
            label=f"continue-state-{state}",
        )
        return state

    state = await detect_page_state(page, detector_kwargs=detector_kwargs)
    await capture_debug_screenshot(
        page,
        enabled=DEBUG_SCREENSHOTS,
        label=f"continue-final-{state}",
    )
    return state
