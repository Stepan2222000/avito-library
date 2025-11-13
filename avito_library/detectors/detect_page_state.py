"""Router that selects the first matching page state detector."""

from __future__ import annotations

import asyncio
from logging import Logger
from typing import Awaitable, Callable, Dict, Iterable, Mapping, MutableSequence, Optional, Sequence, Final

from playwright.async_api import Page, Response

from . import (
    DETECTOR_DEFAULT_ORDER,
    DETECTOR_FUNCTIONS,
    DETECTOR_WAIT_TIMEOUT_RESOLVERS,
)
from ..debug import DEBUG_SCREENSHOTS, capture_debug_screenshot

__all__ = ["DetectionError", "detect_page_state", "NOT_DETECTED_STATE_ID"]


class DetectionError(RuntimeError):
    """Raised when no detector matches the current page state."""


DetectorResult = str | bool
DetectorFn = Callable[[], Awaitable[DetectorResult]]


DEFAULT_ORDER: Sequence[str] = DETECTOR_DEFAULT_ORDER
NOT_DETECTED_STATE_ID: Final[str] = "not_detected"


async def _detect_once(
    page: Page,
    *,
    skip: Iterable[str] | None = None,
    priority: Sequence[str] | None = None,
    detector_kwargs: Mapping[str, Mapping[str, object]] | None = None,
    last_response: Optional[Response] = None,
) -> str:
    """Single detection attempt - returns the identifier of the first detector that matches the page state."""

    # Check for loading spinner first, before any other detectors
    logger = _get_logger_kwarg(detector_kwargs, "loading_detector")

    skip_set = set(skip or ())

    detectors: Dict[str, DetectorFn] = {}

    last_response_detectors = {
        "proxy_block_403_detector",
        "proxy_block_429_detector",
        "proxy_auth_407_detector",
        "removed_or_not_found_detector",
    }
    captcha_detector_id = "captcha_geetest_detector"

    for detector_id, detector_fn in DETECTOR_FUNCTIONS.items():
        if detector_id == captcha_detector_id:
            wait_resolver = DETECTOR_WAIT_TIMEOUT_RESOLVERS.get(detector_id)

            async def _captcha_wrapper(
                fn=detector_fn,
                detector_id=detector_id,
                resolver=wait_resolver,
            ) -> DetectorResult:
                wait_timeout = resolver(detector_kwargs, default=3.0) if resolver else 3.0
                return await fn(
                    page,
                    wait_timeout=wait_timeout,
                    poll_interval=_get_float_kwarg(detector_kwargs, detector_id, "poll_interval", 0.3),
                    logger=_get_logger_kwarg(detector_kwargs, detector_id),
                )

            detectors[detector_id] = _captcha_wrapper
        elif detector_id in last_response_detectors:

            async def _with_response(fn=detector_fn) -> DetectorResult:
                return await fn(page, last_response=last_response)

            detectors[detector_id] = _with_response
        else:

            async def _simple(fn=detector_fn) -> DetectorResult:
                return await fn(page)

            detectors[detector_id] = _simple

    unknown_skips = skip_set - set(detectors.keys())
    if unknown_skips:
        raise ValueError(f"Unknown detectors in skip: {unknown_skips}")

    if detector_kwargs:
        unknown_keys = set(detector_kwargs.keys()) - set(detectors.keys())
        if unknown_keys:
            raise ValueError(f"detector_kwargs provided for unknown detectors: {unknown_keys}")
        unexpected = skip_set.intersection(detector_kwargs.keys())
        if unexpected:
            raise ValueError(f"detector_kwargs provided for skipped detectors: {unexpected}")

    order: MutableSequence[str] = []
    if priority:
        for detector_id in priority:
            if detector_id not in detectors:
                raise ValueError(f"Unknown detector in priority: {detector_id}")
            if detector_id in skip_set or detector_id in order:
                continue
            order.append(detector_id)

    for default_id in DEFAULT_ORDER:
        if default_id in skip_set or default_id in order:
            continue
        order.append(default_id)

    for detector_id in order:
        detector = detectors.get(detector_id)
        if detector is None:
            raise ValueError(f"Detector {detector_id} is not registered")


        result = await detector()
        if result:
            return detector_id if result is True else str(result)

    await capture_debug_screenshot(
        page,
        enabled=DEBUG_SCREENSHOTS,
        label="detect-page-state-no-match",
    )
    return NOT_DETECTED_STATE_ID


async def detect_page_state(
    page: Page,
    *,
    skip: Iterable[str] | None = None,
    priority: Sequence[str] | None = None,
    detector_kwargs: Mapping[str, Mapping[str, object]] | None = None,
    last_response: Optional[Response] = None,
) -> str:
    """Returns the identifier of the first detector that matches the page state.

    If no detector matches, retries up to 3 more times with 20 second delay between attempts.
    """
    max_retries = 3
    retry_delay = 20  # seconds

    for attempt in range(max_retries + 1):  # 1 initial + 3 retries = 4 total attempts
        result = await _detect_once(
            page,
            skip=skip,
            priority=priority,
            detector_kwargs=detector_kwargs,
            last_response=last_response,
        )

        if result != NOT_DETECTED_STATE_ID:
            return result

        # If not the last attempt, wait before retrying
        if attempt < max_retries:
            await asyncio.sleep(retry_delay)

    return NOT_DETECTED_STATE_ID


def _get_float_kwarg(
    detector_kwargs: Mapping[str, Mapping[str, object]] | None,
    detector_id: str,
    key: str,
    default: float,
) -> float:
    if not detector_kwargs:
        return default
    raw = detector_kwargs.get(detector_id, {}).get(key, default)
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _get_logger_kwarg(
    detector_kwargs: Mapping[str, Mapping[str, object]] | None,
    detector_id: str,
    default: Logger | None = None,
) -> Logger | None:
    if not detector_kwargs:
        return default
    maybe_logger = detector_kwargs.get(detector_id, {}).get("logger")
    return maybe_logger if isinstance(maybe_logger, Logger) else default
