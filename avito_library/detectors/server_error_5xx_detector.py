"""Detector for HTTP 5xx server errors (502, 503, 504, etc.)."""

from __future__ import annotations

from typing import Final, Optional

from playwright.async_api import Page, Response

from ..debug import DEBUG_SCREENSHOTS, capture_debug_screenshot

__all__ = [
    "DETECTOR_ID",
    "server_error_5xx_detector",
]

DETECTOR_ID: Final[str] = "server_error_5xx_detector"


async def server_error_5xx_detector(
    page: Page,
    *,
    last_response: Optional[Response] = None,
) -> str | bool:
    """Returns `server_error_5xx_detector` when HTTP status is 5xx (500-599).

    Detects server errors like:
    - 502 Bad Gateway
    - 503 Service Unavailable
    - 504 Gateway Timeout
    - And other 5xx errors

    These errors indicate temporary server unavailability and should be
    handled with retry logic in parsers.
    """
    status = getattr(last_response, "status", None)

    if status is not None and 500 <= status < 600:
        await capture_debug_screenshot(
            page,
            enabled=DEBUG_SCREENSHOTS,
            label=f"detector-server-error-{status}",
        )
        return DETECTOR_ID

    return False
