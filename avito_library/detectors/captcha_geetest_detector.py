"""Detector for Geetest captcha presence on Avito listing pages.

This module implements the behaviour described in
`plan/avito_library/goto_detectors/captcha_geetest.md`. The detector polls
for the simultaneous appearance of all key Geetest DOM nodes within a
configurable timeout window and returns a routing identifier understood by
the higher-level navigation pipeline.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Mapping, Sequence
from logging import Logger, getLogger
from typing import Final, Optional

from playwright.async_api import Error as PlaywrightError, Page

__all__ = [
    "DETECTOR_ID",
    "DEFAULT_WAIT_TIMEOUT",
    "DEFAULT_POLL_INTERVAL",
    "REQUIRED_SELECTORS",
    "resolve_wait_timeout",
    "captcha_geetest_detector",
]

DETECTOR_ID: Final[str] = "captcha_geetest_detector"
DEFAULT_WAIT_TIMEOUT: Final[float] = 3.0
DEFAULT_POLL_INTERVAL: Final[float] = 0.3
REQUIRED_SELECTORS: Final[Sequence[str]] = (
    "div.geetest_box",
    "div.geetest_slice_bg",
    "div.geetest_bg",
    "div.geetest_slice",
    ".geetest_track",
)


def resolve_wait_timeout(
    detector_kwargs: Mapping[str, Mapping[str, object]] | None,
    *,
    default: float = DEFAULT_WAIT_TIMEOUT,
) -> float:
    """Extracts `wait_timeout` for the captcha detector from detector_kwargs."""

    if not detector_kwargs:
        return default
    raw_cfg = detector_kwargs.get(DETECTOR_ID)
    if not isinstance(raw_cfg, Mapping):
        return default
    raw_timeout = raw_cfg.get("wait_timeout", default)
    try:
        timeout = float(raw_timeout)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return timeout if math.isfinite(timeout) and timeout >= 0 else default


async def captcha_geetest_detector(
    page: Page,
    *,
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    logger: Optional[Logger] = None,
) -> str | bool:
    """Detects Geetest by checking that all required selectors are present."""

    log = logger or getLogger(__name__)
    timeout = wait_timeout if wait_timeout >= 0 else 0.0
    interval = poll_interval if poll_interval > 0 else DEFAULT_POLL_INTERVAL

    deadline = time.monotonic() + timeout
    # Perform an immediate check before sleeping to avoid unnecessary delay.
    while True:
        if await _all_selectors_present(page):
            log.info("Geetest captcha detected via selectors: %s", REQUIRED_SELECTORS)
            return DETECTOR_ID

        if time.monotonic() >= deadline:
            return False

        remaining = deadline - time.monotonic()
        # Avoid tight loops close to the deadline.
        await asyncio.sleep(min(interval, max(remaining, 0.05)))


async def _all_selectors_present(page: Page) -> bool:
    """Returns True when every required selector is found on the page."""

    try:
        results = await asyncio.gather(
            *[page.query_selector(selector) for selector in REQUIRED_SELECTORS],
            return_exceptions=True,
        )
    except PlaywrightError:
        return False

    for selector, result in zip(REQUIRED_SELECTORS, results):
        if isinstance(result, Exception):
            # If Playwright raised for a selector, treat as missing.
            return False
        if result is None:
            return False
    return True
