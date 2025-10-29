"""High-level orchestrator for solving Geetest captcha flow."""

from __future__ import annotations

from typing import Tuple

from playwright.async_api import Page

from ..debug import DEBUG_SCREENSHOTS, capture_debug_screenshot

from .solve_slider_once import solve_slider_once
from ..utils import press_continue_and_detect
from ..detectors import (
    detect_page_state,
    CAPTCHA_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
)
__all__ = ["resolve_captcha_flow"]

_CAPTCHA_STATE = CAPTCHA_DETECTOR_ID
_CONTINUE_STATE = "continue_button_detector"
_BLOCKING_STATES = {
    _CAPTCHA_STATE,
    _CONTINUE_STATE,
    PROXY_BLOCK_429_DETECTOR_ID,
}


async def resolve_captcha_flow(page: Page, *, max_attempts: int = 3) -> Tuple[str, bool]:
    """Attempt to resolve Geetest captcha by combining button presses, solver and detectors."""

    initial_state = await press_continue_and_detect(page)
    await capture_debug_screenshot(
        page,
        enabled=DEBUG_SCREENSHOTS,
        label=f"captcha-flow-initial-{initial_state}",
    )
    if initial_state not in {_CAPTCHA_STATE, _CONTINUE_STATE, PROXY_BLOCK_429_DETECTOR_ID}:
        # Нет капчи и нет 429 — считаем, что всё успешно.
        await capture_debug_screenshot(
            page,
            enabled=DEBUG_SCREENSHOTS,
            label="captcha-flow-no-block",
        )
        html = await page.content()
        return html, True

    attempts_left = max(0, max_attempts)
    last_html: str | None = None
    while attempts_left:
        try:
            html, solved = await solve_slider_once(page)
        except Exception:
            html = await page.content()
            solved = False
        last_html = html
        if solved:
            final_state = await detect_page_state(page)
            if final_state not in _BLOCKING_STATES:
                await capture_debug_screenshot(
                    page,
                    enabled=DEBUG_SCREENSHOTS,
                    label=f"captcha-flow-solved-{final_state}",
                )
                return html, True
        await capture_debug_screenshot(
            page,
            enabled=DEBUG_SCREENSHOTS,
            label=f"captcha-flow-attempt-{max_attempts - attempts_left + 1}",
        )
        attempts_left -= 1

    if last_html is None:
        last_html = await page.content()
    await capture_debug_screenshot(
        page,
        enabled=DEBUG_SCREENSHOTS,
        label="captcha-flow-failed",
    )
    return last_html, False
